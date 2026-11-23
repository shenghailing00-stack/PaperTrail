# TASK-006 — 持久化阅读库

**状态：** 实施及审查修订完成；完整 pytest 393 passed，待 Owner 验收（未提交、未推送）
**设计：** Claude Code（2026-11-23）
**实施者：** Codex；**审查：** Claude Code
**分支：** TASK-UI-01 合并后，从当时的 `main` 拉出 `task-006` 分支

---

## 1. 要解决的问题

现在的阅读清单只保存在 Streamlit 的会话里（`ui.py` 第 339 行的 `st.session_state["reading_lists"]`），并且按 "数据库 + run" 隔离：

- 刷新或关闭浏览器，没有下载的选择和阅读理由就丢了；
- 同一篇论文在两次探索中都出现时，需要分别收藏、分别写理由；
- 没有"读到哪了"的记录。

**目标：** 一个跨 run、跨数据库、关掉再打开也还在的**个人阅读库**。

## 2. 范围

| 版本 | 内容 | 本周是否做 |
|---|---|---|
| **v1（必做）** | 收藏、备注、阅读状态（待读 / 在读 / 已读）、记录"从哪次探索发现的"、关掉后能恢复、导出 CSV/Markdown、在候选表和详情中标出"已收藏" | ✅ |
| **v1（顺带做）** | 优先级（高 / 中 / 低）。只是多一列，成本很低 | ✅ |
| 以后 | 自定义标签、导入、笔记历史、多设备同步 | ❌ |

标签之所以放到以后，是因为它需要多对多的表、标签管理界面和筛选逻辑，工作量是其他功能的好几倍。

## 3. 核心设计决定：阅读库放在独立的数据库文件里

**不要把阅读库写进样本数据库。** 原因如下：

1. **样本库只读是一条已有的承诺。** `PaperStore.open_readonly`（`store.py` 第 42 行）用 `mode=ro` 打开，并且明确"不迁移"；README 和测试都依赖这一点，它保证打开一个样本永远不会改动它。如果把阅读库写进样本库，这个承诺就被打破了，而且旧的数据库还需要迁移。
2. **跨库是需求本身的要求。** 用户可能用不同的数据库探索不同的方向，比如 `tactile.db` 和 `manipulation.db`。阅读库写在任何一个样本库里，都没法同时收藏另一个库里的论文。
3. **职责清晰。** 样本库是"采集到的事实"；阅读库是"我的个人判断"，比如备注、状态。两者的生命周期不同。

**位置：**

- 真实数据：`output/library.db`
- 演示数据：`output/ui-demo/library-demo.db`。**演示和真实的阅读库必须分开**，否则合成论文会混进用户的真实阅读库。

`output/` 已经被 Git 忽略，个人笔记不会被提交。

## 4. 数据结构

新模块 `src/papertrail/library.py`，只用标准库 `sqlite3`，风格参考 `store.py`。每次操作打开一次连接，用完即关，**不缓存连接**（与 `load_view` 的原则一致）。

```sql
PRAGMA user_version = 1;          -- 为以后可能的迁移留版本号
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS library_items (
    paper_id      TEXT PRIMARY KEY,
    -- 加入时的元数据快照：打开阅读库时不需要样本库也能显示
    title         TEXT,
    year          INTEGER,
    authors_json  TEXT,                 -- 作者名列表
    source_url    TEXT,                 -- 只存 source_link() 校验通过的链接，否则存 NULL
    -- 用户自己的字段
    status        TEXT NOT NULL DEFAULT 'to_read'
                  CHECK (status IN ('to_read', 'reading', 'read')),
    priority      TEXT CHECK (priority IN ('high', 'medium', 'low')),   -- 可以为空
    note          TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 2000),
    added_at      TEXT NOT NULL,        -- UTC ISO 时间
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_sources (   -- 这篇论文是从哪些探索中发现的
    paper_id   TEXT NOT NULL REFERENCES library_items(paper_id) ON DELETE CASCADE,
    db_path    TEXT NOT NULL,           -- 样本库的绝对路径（与 view["identity"][0] 一致）
    run_id     INTEGER NOT NULL,
    seed_titles TEXT,                   -- 那次探索的种子标题，用于显示"来自：GelSight · Touch and Go"
    category   TEXT,                    -- 种子 / 前期工作 / 后续研究
    relation   TEXT,                    -- relation_text() 的结果，例如"共同：引用 2/2 篇种子"
    added_at   TEXT NOT NULL,
    PRIMARY KEY (paper_id, db_path, run_id)
);
```

**写入规则：**

- **加入阅读库**：`library_items` 已有这篇论文时，**只更新元数据快照**（标题、年份、作者、链接），**不覆盖**用户填写的 `status`、`priority`、`note`；`library_sources` 中增加一条来源，已经存在就忽略。整个过程在一个事务里完成。
- **修改状态、优先级、备注**：只改这些字段，并更新 `updated_at`。
- **移出阅读库**：删除这一条，`library_sources` 会级联删除。
- 所有写入都要校验输入：状态和优先级只接受上面列出的值；备注最长 2000 字；`paper_id` 必须来自当前打开的样本（与现在 `export_reading_list` 第 349 行拒绝"不属于当前 run 的选择"一样）。
- 写入阅读库之前，对所有文字调用现有的 `safe()` 处理（防止密钥被意外写入）。

## 5. 界面

遵循 TASK-UI-01 的设计语言：简洁，信息按需展开。

### 5.1 收藏入口

- **论文详情卡片**：现在的"加入阅读清单"按钮改为 **"加入阅读库"**。已经收藏过的论文显示为 **"✓ 已在阅读库 · 在读"**，并提供"移出"操作。
- **候选论文表**：增加一列很窄的 **"★"**，已收藏的论文显示 ★。这样打开新的样本时，就能直接看到哪些论文以前已经收藏过，这正是跨 run 阅读库最直观的价值。

### 5.2 "阅读库"标签页（替换现在的"阅读清单"）

```
阅读库 · 12 篇                                   [导出 CSV] [导出 Markdown]
┌ 全部 | 待读 | 在读 | 已读 ┐   ☐ 只看当前样本     🔍 搜索标题或备注
┌───────────────────────────────────────────────────────────────┐
│ ★ 标题                      年份  状态▾   优先级▾  备注              来源      │  ← st.data_editor
│   CoRA-VTLA: Contact-…      2026  在读    高       看接触记忆的设计   2 次探索   │    状态/优先级/备注可直接编辑
│   GelSight: High-Res…       2017  已读    —        经典传感器        1 次探索   │
└───────────────────────────────────────────────────────────────┘
选中一行 → 下方显示来源明细：来自哪些探索（种子标题、日期、关系说明）+ 打开原文 + 移出阅读库
```

- 用 `st.data_editor` 直接编辑表格中的状态（`SelectboxColumn`）、优先级（`SelectboxColumn`）和备注（`TextColumn`，`max_chars=2000`）。标题、年份、来源这几列设为只读。
- 修改**立即写入**阅读库，不需要额外的"保存"按钮。实现时注意：`data_editor` 的修改应通过它的 `key` 和 `on_change` 回调读取差异，只写入有变化的行。
- "只看当前样本"打开时，只显示 `library_sources` 中包含当前 `(db_path, run_id)` 的论文。
- 阅读库为空时，只显示一行引导："在引用网络或候选论文中点"加入阅读库"。"
- **删除** TASK-UI-01 留下的那句"阅读清单只保存在当前页面，关闭前请先下载"，因为现在会自动保存了。
- 在页面底部用一行很小的字显示阅读库文件的位置，方便用户备份。

### 5.3 不需要打开样本也能访问阅读库

阅读库不依赖任何样本，所以在首页（还没打开样本时）也应该可以进入。建议在侧栏底部放一个入口"我的阅读库 · 12 篇"，点击后在主区域只显示阅读库。

## 6. 导出

- 导出的范围是**当前筛选后看到的条目**。
- 字段：标题、年份、来源链接、状态、优先级、备注、发现于（来源列表，例如"run #5 · GelSight · Touch and Go"）、关系说明、paperId。
- **必须复用**现有导出中的安全处理：CSV 公式转义（`cell()`）、Markdown 字面化（`md()`）、来源链接校验（`valid_source_url()`）。做法是把这几个内嵌函数提升为模块级的私有函数，**现有 `export_reading_list` 的输出必须保持逐字节不变**（现有测试会验证这一点）。
- Markdown 开头保留一句话："依据 PaperTrail 采集样本与个人选择，不代表领域整体或必读推荐。"如果导出内容来自截断样本，列出涉及的 run 及其采集范围。

## 7. 必须保持的约束

- **样本库仍然只读**：加入、修改、删除阅读库时，样本库文件的内容（SHA-256）不能有任何变化。要有测试验证这一点。
- 演示阅读库和真实阅读库完全分开。
- 阅读库的任何操作都**不联网**。
- 不修改 `collector`、`store`、`analysis`、`discovery`。
- 不新增依赖。

## 8. 测试要求

**`tests/test_library.py`（新建，全部使用临时目录）：**

- 新建库时 `user_version = 1`，两张表都存在；
- 同一篇论文从两个 run 加入：只有 1 条 item、2 条 source，用户字段不被覆盖，元数据快照会更新；
- 状态、优先级的非法值被拒绝；备注超过 2000 字被拒绝；
- 删除时来源级联删除；
- 关闭连接、重新打开后数据仍在（模拟重启）；
- **加入、修改、删除前后，样本库文件的 SHA-256 不变**；
- 演示模式和真实模式写入的是不同的文件；
- 导出：公式转义、Markdown 注入、非法链接不生成超链接；现有 `export_reading_list` 的输出不变。

**`tests/test_ui.py`（AppTest）：**

- 在详情中加入阅读库 → 阅读库标签页出现这篇论文；
- 修改状态 → **新建一个 AppTest 实例**（模拟刷新页面）后，状态仍然保留；
- 已收藏的论文在候选表中显示 ★；
- 没有打开样本时，也能打开阅读库。

完整 `pytest` 必须全部通过。

**浏览器实测：** 用 run 5 收藏 3 篇论文并设置状态和备注 → 关闭 Streamlit、重新启动 → 阅读库内容还在 → 导出 CSV 和 Markdown 检查内容。截图保存到 `output/task006/`。

## 9. 文档

- `docs/USER_MANUAL.md`：重写第 10 节（阅读清单 → 阅读库）；删除第 14 节中"阅读清单丢失"这一行；第 15 节"文件位置"中加上 `output/library.db`。
- README：在 UI 部分说明阅读库及其位置。

## 10. 交付

按 AGENTS.md 的交接格式汇报，并在本文件末尾追加实施记录。不要提交，也不要推送；等 Claude Code 审查、Owner 确认后再合并。

---

## 附：Owner 已确认的决定（2026-11-23）

1. **范围**：v1 按第 2 节实现（收藏 + 备注 + 状态 + 优先级 + 来源记录 + 导出）；标签留到以后。
2. **阅读库位置**：固定为 `output/library.db`（演示数据为 `output/ui-demo/library-demo.db`），界面中不提供修改路径的设置。
3. **替换**：用"阅读库"标签页替代现有按 run 的"阅读清单"；按单个 run 导出，通过"只看当前样本"筛选后再导出来实现。

## 11. 实施与审查修订记录（2026-11-23，Codex）

**分支：** `task-006`。未提交、未推送、未合并。
**状态：** 初版实现及本轮三处审查修订完成；最终完整测试结果见下文。本记录补齐此前工作区断连时尚未写入的实施交接。

### 改动路径与行为

- `src/papertrail/library.py`：独立 SQLite 阅读库，真实路径 `output/library.db`、演示路径 `output/ui-demo/library-demo.db`；每次操作独立打开并关闭连接。按 paperId 跨 run、跨样本库去重，重复收藏只更新元数据快照、追加来源，不覆盖状态、优先级或备注。支持筛选、级联移出、CSV/Markdown 导出与所有写入文字脱敏。
- `src/papertrail/ui_library.py`：阅读库页面及收藏控件；表格即时编辑状态、优先级、备注，只提交变化字段；按状态、当前样本和标题/备注筛选；查看来源、打开原文、确认移出；首页无需打开样本也可访问真实阅读库。
- `src/papertrail/ui.py`：以阅读库替代会话阅读清单，候选表显示收藏 ★。同时修复首页“试用演示”错误捕获，并在概况元信息末尾显示 run 编号。
- `src/papertrail/ui_service.py`：种子最后绘制，节点增至 24 px 并加 2 px 白色描边，指向种子的箭头使用 14 px standoff；提取 `_cell`、`_md`、`_markdown_record` 供新旧导出共用，旧导出格式不变。
- `tests/test_library.py`、`tests/test_ui.py`、`tests/fixtures/reading_export_legacy.*`：数据库、UI、原导出字节基准及本轮回归测试。
- `README.md`、`docs/USER_MANUAL.md`：说明持久化位置、即时编辑、筛选导出、备份和确认移出；删除阅读清单会话丢失说明。

### 本轮审查意见逐条落实

1. 详情收藏入口和阅读库详情共用 `confirm_remove`：先打开 `st.popover("移出")`，只有点击“确认移出”才调用 `library.remove`。确认控件 key 绑定 paperId，避免切换论文时沿用另一篇论文的确认。三组 AppTest 覆盖网络详情、候选入口和阅读库详情，发送真实 popover-open widget 事件，断言仅打开时无删除调用、数据保留；确认后恰好删除一次。
2. `export_items` 的 Markdown 采集范围只输出 `Path(db_path).name`，不输出样本绝对目录；内部来源仍保留绝对路径，维持跨库区分和当前样本筛选。新增导出测试断言包含文件名、不含样本目录或完整路径。
3. 表格空优先级使用空字符串，编辑回调把清空映射为数据库 NULL；新增“初始空白 → 高 → 清空 → rerun 仍空白”的回归测试。
4. 采用可选建议：删除阅读库表格中重复的 ★ 列，候选表的收藏标识保持。

### 第 7 节约束逐条核对

| 约束 | 实现与验证 |
|---|---|
| 样本库只读，SHA-256 不变 | 阅读库使用独立连接；拒绝把样本路径当阅读库。测试分别在加入、修改、移出后比较样本 SHA-256；真实 run 5 浏览器操作前后哈希相同，见 `output/task006/browser-verification.json` |
| 演示/真实阅读库隔离 | 固定为两个不同文件；写入层还拒绝同一阅读库混合演示和真实来源；测试覆盖路径、个人字段和链接隔离 |
| 阅读库不联网 | 收藏、编辑、查询、移出、导出只访问本地快照和 SQLite；测试将 HTTP 请求替换为抛错 mock，全部离线 |
| 不改业务模块 | `git diff --name-only -- src/papertrail/collector.py src/papertrail/store.py src/papertrail/analysis.py src/papertrail/discovery.py` 无输出 |
| 不新增依赖 | requirements 未变；持久化用标准库 sqlite3，界面使用现有 Streamlit/Plotly |

### 第 8 节测试与证据

- 库初始化：版本 1、两张表；跨两个实际 run 收藏：1 item / 2 sources，快照更新、个人字段保留；非法状态/优先级和超长备注拒绝；来源级联删除；关闭连接后恢复；写入事务失败时回滚；跨模式隔离，均有临时目录测试。
- 导出：CSV 公式转义、Markdown 注入、非法链接保护、筛选范围、截断说明均有测试。`test_legacy_export_bytes_unchanged` 将旧导出的 CSV/Markdown 与改动前保存的基准文件逐字节比较。
- AppTest：详情与候选收藏、星标、新建 AppTest 实例恢复状态/优先级/备注、无样本访问阅读库、跨库来源筛选、只保存变化行、清空优先级、移出确认和首页演示异常均覆盖。
- 初版完整测试：`.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task006/pytest-cache`，**388 passed in 78.71s**，日志 `output/task006/pytest-full.txt`。
- 本轮完整测试：`.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task006/pytest-cache`，**393 passed in 62.59s**；日志 `output/task006/review-pytest-full.txt`。此前阅读库/UI 定向测试为 **114 passed in 24.72s**，日志 `output/task006/review-targeted.txt`。
- `git diff --check` 通过；`output/library.db` 及验收产物由 Git 忽略。

初版开发中的旧 UI 测试曾有 7 项仍断言会话阅读清单控件而失败，已改为验证持久化语义。事务测试曾发现 `INSERT OR IGNORE` 会吞掉非重复来源错误，已改为明确的复合主键 `ON CONFLICT ... DO NOTHING`，回滚测试通过。测试目录写入受系统限制时，Owner 应用了准备好的文件；正式测试前已逐字节核对一致，并将临时 `.py` 副本改为 `.py.txt`，避免重复收集。未跳过失败测试。

### 真实浏览器验收（初版，已完成）

使用 `output/task005-real/task005-real.db` 的 run 5，实际收藏 GelSight、Touch and Go、Connecting Look and Feel 三篇论文，编辑为在读/已读/待读，填写三条“验收：”备注并设置高优先级。实际停止并重新启动本次验收的 Streamlit 服务，从无样本首页进入“我的阅读库”，三条记录及所有个人字段一致；下载 CSV/Markdown 后逐字节核对服务生成内容，并检查 run #5 来源和采集范围。

样本 SHA-256 前后均为：`B738EDA7D7FBAB47CE4537DBF753AEBF82A68284A5FCFEC428A440A07DDC8A63`。

| 证据 | 路径（均在 output/task006/） |
|---|---|
| 重启前 / 后截图 | `library-before-restart.jpg` / `library-after-restart.jpg` |
| 网络种子可见性 | `network-seed-visibility.jpg` |
| 候选收藏标识 | `candidates-saved-stars.jpg` |
| 浏览器下载内容 | `browser-export.csv` / `browser-export.md` |
| 重启、下载和样本哈希核对 | `browser-verification.json` |

以上截图和下载文件是初版证据，尚显示本轮修改前的 ★ 列、空优先级或导出目录；本轮修订由新增自动化测试验证，不将旧截图称为修订后的截图。三个验收收藏及备注仍保留在真实阅读库中。未请求真实论文 API。

### 设计取舍、Owner 阅读与下一步

1. 个人判断与采集事实分库保存，无需迁移样本；来源表额外保存 `sample_status`、`collection_scope`、`demo`，保证脱离样本仍可正确导出范围及演示标识。
2. `data_editor` 回调把行号映射到当时渲染的快照，只提交差异；修改后更换 editor key，避免旧事件在筛选后作用于另一篇论文。
3. 旧导出保留格式和安全处理；新导出省略私有目录，来源身份仍在库内完整保存。未增加标签、导入或同步功能。

建议阅读 `library.py:add/update_many/export_items`（事务、个人字段保护和导出）、`ui_library.py:apply_editor_changes/confirm_remove`（差异保存和确认）、`tests/test_library.py:test_sample_sha256_unchanged_after_each_operation/test_legacy_export_bytes_unchanged`（两个不变承诺）。
手动检查：打开任意收藏，点“移出”再关闭确认框，确认备注和状态仍在；清空优先级并刷新，确认显示为空白。
下一步为 Claude Code 复审及 Owner 验收。本地阅读库可在退出 Streamlit 后复制备份；没有多设备同步。样本仍受原采集预算限制。
