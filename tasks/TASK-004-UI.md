# TASK-004 — Streamlit MVP 与阅读清单导出

**状态：** TASK-004 已通过独立审查（Owner 提供：319 项，无 Blocker）；TASK-004.1 限定修复完成，等待最终验收。
**日期：** 初版 2026-11-10；限定修复 2026-11-11（America/Los_Angeles）。
**主要实现者：** Codex，唯一编辑者，未启动子代理。
**边界：** 没有真实论文 API 查询/采集，没有 Commit/Push、云部署、登录或后续扩展功能。

## 开发前检查与方案

已读 AGENTS.md、PROJECT_BRIEF、README、TASK-002B/TASK-003 记录及
api/collector/store/analysis/discovery 模块。`git status --short` 初始为空，HEAD 为
`ce8fb46 feat: add explainable literature discovery and filtering`，确认 TASK-003 已提交。
历史任务记录里的“等待验收”是当时状态；本轮以 Owner 已通过独立审查的说明和当前 Git 状态为准。

开发前声明单页面方案、改动模块、验收范围后直接实施。四个标签页：引用网络、候选论文、
样本统计、阅读清单；侧栏负责演示、已有样本、新建采集。无架构冲突，不改存储 schema。
Owner 另外明确授权查阅官方文档和安装必要依赖；此授权不包含真实论文 API 测试。

## 实际文件

| 文件 | 行为 |
|---|---|
| `streamlit_app.py`（新增） | 仓库启动入口，加入 src 导入路径 |
| `src/papertrail/ui.py`（新增） | 入口、查询确认、四个标签页、状态与控件 |
| `src/papertrail/ui_service.py`（新增） | 输入适配、显式查询/采集、演示与只读加载、图数据、展示字段、CSV/Markdown |
| `.streamlit/config.toml`（新增） | 默认本机监听、headless、关闭使用统计上传，无密钥 |
| `src/papertrail/api.py` | 增加最小 `S2Client.search_titles()`，复用已有 HTTP 策略 |
| `requirements.txt` | 增加 Streamlit 和 Plotly |
| `tests/test_ui.py`（新增） | 41 项离线单元/界面验收 |
| `tests/test_discovery.py` | 仅追加 1 项种子间引用不增加非种子候选数的回归 |
| `README.md` | 启动、完整离线流程、真实模式操作、状态语义与限制；纠正“共同”的默认文案 |
| 本文件（新增） | 方案、证据、取舍、人工验收及课堂流程 |

未修改 collector/store/analysis/discovery 实现、原 fixture、CLI 和无关文件。
测试临时文件、演示库、截图保存在临时目录或 Git 忽略的 output 中，不加入源码交付。

## 环境与运行

实测 Python 3.11.7、Streamlit 1.67.0、Plotly 6.9.0、NetworkX 3.6.1。
使用仓库现有 `.venv`，不用激活脚本或修改 PowerShell 执行策略：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
# 浏览器打开 http://127.0.0.1:8501
```

首次 pip 安装受执行环境网络限制，报 WinError 10013，未安装成功；在 Owner 已批准范围内
通过带权限的同一虚拟环境安装成功。没有全局安装、关闭证书校验或修改用户 Git 配置。
`python -m pip check` 返回 `No broken requirements found.`。

## 关键交互与取舍

1. **复用业务逻辑。** `load_view` 使用 `discover()` 提供完整样本结果与种子/候选；
   `load_graph()` 提供经过既有校验的有向拓扑。筛选排序复用 `filter_papers/rank_papers`。
   图只限制显示，不删底层节点；悬浮和详情统一使用本次 discover 读到的元数据。
   两次只读打开的 run 成员固定，元数据不是历史快照，也不承诺跨两个连接的一次全局事务。
2. **显式动作与会话缓存。** 页面只在查询、采集按钮内调用网络方法。
   查询后每项候选默认未选择，必须确认；输入或候选改变后原确认失效。
   查询最多 3 个端点、共 9 次含重试尝试，标题只取首批 5 条，不自动翻页或用最佳匹配代替确认。
   采集复用原有一层扩展、来源绑定、7 天缓存、配额与失败处理；确认时不另写库。
   当前分析结果只保存在该浏览器的 session_state；没有跨用户全局缓存、缓存连接或缓存 HTTP 客户端。
3. **图交互与排序透明。** NetworkX 固定随机种子布局，Plotly 菱形/圆形区分种子，箭头指向被引用者。
   `on_select` 回调按节点 customdata 中的 paperId 更新下拉框及详情；提供下拉操作替代精确点击。
   显示 3–200 个节点、种子优先，其余 ID 顺序仅控制显示。两类候选独立排名，不让默认参考排序隐藏引用种子的论文。
   “共同”仅用于至少 2 颗种子的关系，显示如 2/3；同分保留并列，缺失值不当作 0。
4. **阅读清单与来源。** 以绝对数据库路径 + run_id 隔离各自选择与备注，筛选不会清空清单。
   导出同时保留关系依据、手动选择、来源、采集范围和 run_id，不称为经典/baseline/必读。
   对合成论文始终标记演示数据，不生成虚构论文链接。真实行优先 DOI，再 arXiv，再安全 HTTP(S)
   来源 URL 或 S2 ID 链接。CSV 为 UTF-8 BOM，防止标题/备注被表格程序解释为公式；Markdown 转义表格内容。

UI 全局被引预算默认 150、范围 0–200；请求尝试预算默认 100、范围 1–100；每种子参考上限
10000、每页 100。正文明确配额均分、按 API 顺序采样、重复/缺 ID 也占配额。
开启数值筛选会排除未知值；整数 number_input 的上下限、默认值、步长均为整数。

完整/截断样本复用 TASK-002B 的严格校验；失败/不一致样本不展示旧图冒充新样本。
顶部保持合成数据声明、中文正常预算截断警告和数据质量摘要，原始 warnings 完整保存在展开区。
统计始终使用完整当前 run，包含论文/边/种子/候选数量、年份、缺失数据，不外推领域趋势。
现有 API Key 仅从环境读取，用于请求头和输出脱敏，不在页面设置、输出或持久化。

## 自动化证据与失败修复

```powershell
# 改动前基线
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task004-pytest-cache
# 277 passed in 62.23s

# TASK-004 初版完整回归（277 原有 + 1 种子回归 + 41 UI；最新结果见 TASK-004.1）
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task004-pytest-cache
# 319 passed in 48.69s

git diff --check
# 通过；原有 safe.directory/LF-CRLF 提示未改动配置
git ls-files -u
# 空，无冲突
```

首轮 UI 为 32 passed / 2 failed。根因：空 DOI 抛出的 argparse.ArgumentTypeError 没有转换为
页面 ValueError；未选候选直接确认后异常跨过整个侧栏，采集按钮不再呈现。
分别在输入适配层转换为明确提示、在确认动作内处理错误，第二轮 34 passed。
随后增加查询形状/空结果、脱敏及缺库只读测试，UI 共 41 项，完整 319 项通过。
最后小修进一步容忍查询中非字符串 paperId，并明确备注提交后下载；受影响 UI 测试另行重跑：
`python -m pytest tests/test_ui.py -q -o cache_dir=output/task004-pytest-cache`，**41 passed in 10.11s**。

| 验收点 | 离线证据 |
|---|---|
| 演示不联网、重复打开复用 run | 禁止 requests.Session.request；禁止 API 客户端构造；数据库字节不变 |
| 1–3 输入/预算校验 | DOI/arXiv/标题参数化、空输入/超量/控制字符/错误预算先于客户端创建拒绝 |
| 标题人工确认 | 空选择不允许采集、选第二项传第二 ID、修改输入/选择后失效 |
| HTTP 安全复用 | search_titles 路径与字段、timeout/不重定向、RequestBudget、Key 脱敏 |
| bounded/failed | 真正 collector + FixtureClient 创建样本；AppTest 检查截断警告、失败无旧图 |
| 双类别、整数筛选和排序 | 各自 C/D、对应关系排序、组合条件、传参 spy、整数类型 |
| 节点选择 | 图 customdata→对应 ID；AppTest 下拉到 C，检查摘要；显示限制不改原图 |
| 清单/导出 | 加入、备注、去重、两种格式、中文/换行/分隔符、公式转义、脱敏、foreign ID 拒绝 |
| 重绘无重复采集 | mock 查询 + 真实离线 collector；点击一次后多次控件重绘调用数保持 1 |
| run 与来源隔离 | 同 run_id 不同 DB 的清单分离；fixture DB 拒绝真实来源且未先发请求 |
| 种子间引用回归 | 现有 fixture B→A，A/B 是种子，候选仍只有 C/D，两类各 1 |

AppTest 是控件模拟，不等于真实浏览器；上述 mock 不证明真实查询、分页、DOI 或限流可用。

## 本机浏览器实际验收

本机 Streamlit 通过 `127.0.0.1:8501` 启动，headless 且关闭统计上传；使用内置浏览器，
仅本地 fixture，未勾选真实联网授权入口，未访问真实论文 API。

- 打开演示看到 4/5/2/2/2 五个指标和合成数据声明。
- 调整显示上限 100→99 后仍为同一样本。
- **实际点击蓝色节点**，详情与下拉选中项变为 Synthetic citing paper，年份未知、
  共同引用 2/2 颗种子、样本入度 0 / 出度 2。不是只验证静态图片或人工注入选择事件。
- 点击加入清单，切到阅读清单后显示同一篇；输入“课堂演示：先核对引用方向”，按 Tab 提交。
- CSV/Markdown 下载事件成功，下载到用户 Downloads 的 `papertrail-run-1.csv/.md`；
  读取两份文件核对选中 D、中文备注、关系依据、演示标记与空真实链接。没有删除这些演示文件。
- 切换候选页，两类各 1；年份筛选启用后出现整数 1900/2100，候选各变成 0，顶部样本总数不变。
- 样本统计实际显示 2024/2025 各 1 的柱图，并有采集样本声明。阅读清单在切页/筛选后保留。

浏览器 checkbox 的首次语义操作未改变状态；观察最新画面后点选实际控件成功，未将失败尝试记为通过。
切换统计页时图表有短暂布局加载，稍后实际显示正常；不是以空白首帧作为验收。
截图：`output/task004-evidence/reading-list-browser.png`。
浏览器下载实测后，导出补充了“采集范围”列，由最终离线导出测试验证；没有把它声称为第二次浏览器下载实测。

## 可复现离线与 3–5 分钟课堂演示

完整步骤见 README 的“完整离线演示”；依赖装好后可以断开外网并启动页面。
演示数据明确是合成 fixture，非真实研究结论，不要求输入 API Key。

| 时间 | 操作与讲解 |
|---|---|
| 0:00–0:40 | 打开演示，说明两颗种子、一层采样及“合成数据”标记；核对 4 论文/5 边 |
| 0:40–1:40 | 指出橙色种子、蓝色候选和引用方向；点击 D 查看“引用 2/2”，再下拉 C 查看摘要 |
| 1:40–2:40 | 两类候选分开展示；说明种子不进候选数、共同必须 ≥2；启用年份筛选得到空结果，再关闭，解释未知不等于 0 |
| 2:40–3:20 | 看年份分布与缺失年份，强调仅采集样本，不是领域趋势；说明正常截断不等于失败 |
| 3:20–4:30 | 手动加入 C/D，填写阅读理由后 Enter/点击空白处提交，下载 CSV/Markdown；展示依据与合成标记 |

如需离线演示 bounded：先运行下面命令，再从页面“已有样本”打开输出的实际 run_id：

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-bounded-demo.db --citation-budget 1 --page-size 2
```

## 未完成的人工验收与限制

- **真实端到端待授权：** 选 1–3 篇真实 DOI/arXiv/标题；核对作者与年份，明确选候选、确认、采集；
  观察真实 run、缓存、429/错误行为及源链接。此轮 0 次真实论文 API，不用 mock 代替成功证据。
- **更大样本待体验：** 用已授权保存的较大 run，设置图上限 50/100/200，核对流畅度、箭头重叠与选择体验；
  自动化验证限制不改分析，未声称验证所有图规模/浏览器。
- **不同终端待验收：** 本轮是 Windows 内置浏览器；课堂 Chrome/Edge、屏幕尺寸及目标 Excel/WPS 的 CSV 显示须 Owner 实测。
- **会话持久性有限：** session_state 保护重绘和切样本，不是持久阅读清单数据库；硬刷新、断线或重启可能丢失未下载选择。
  输入阅读理由后先 Enter/失焦提交，等重绘完成再下载；CSV/Markdown 是可持久交付物。
- **搜索和统计局限：** 首批 5 条 relevance search，不是精确标题唯一匹配；没找到时应细化输入。
  元数据最新已知，单层采样缺候选间引用，API 返回顺序不等于价值；fixture 非种子年份/引用数缺失是演示特性。
- **缓存更新方式：** 只读分析在本次会话中保持稳定；要看其他进程更新后的元数据需显式重新打开。
  只支持单人本地课程 MVP，不承诺多用户并发/跨进程锁定、签名防篡改或完整研究覆盖。

## Owner 学习与交接

建议阅读 `ui.py::collection_panel` 的显式按钮与确认状态、`ui.py::graph_panel` 的选择回调，
以及 `ui_service.py::export_reading_list` 的关系依据/来源/序列化边界。
手工问题：选中 C 后开启年份筛选，为什么候选表为空而阅读清单仍有 C、样本总数仍为 4？
答案应区分样本、筛选视图和手工选择，而不是把筛选当成删除论文。

下一步仅 Owner 验收与独立只读 Review；不继续未来功能、不 Commit/Push。

## 本轮查阅的官方资料

- [Streamlit Plotly 选择事件](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart)：`on_select`、customdata 和 callback。
- [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)：模拟控件验收的能力范围。
- [Plotly annotations](https://plotly.com/python/text-and-annotations/)：有向箭头的显示方式。
- [Semantic Scholar 官方 API 规范](https://api.semanticscholar.org/graph/v1/swagger.json)：`/paper/search` relevance 候选、fields/limit/offset；未调用论文查询端点。

本轮官方文档核验日期 2026-11-10；不据此声称真实 API 可用或条款/账户额度验收通过。

## TASK-004.1：性能、功能完整性与交互安全限定修复

2026-11-11，Owner 根据独立审查要求在提交前完成 M1–M6。开始时工作区包含 TASK-004 的
未提交实现，HEAD 仍为 `ce8fb46`；这不是干净工作区，保留所有原有改动。
开发前已说明最小改动方案与文件范围。Codex 仍为唯一编辑者，未启动子代理；未修改
collector/store/analysis/discovery、数据库 schema、依赖或原 fixture；未调用真实论文 API、Commit 或 Push。

### 本次文件与对应验证

| 文件 | 本次改动 |
|---|---|
| `src/papertrail/ui_service.py` | 批量箭头与单项图缓存；复用关键词统计；操作状态机；元数据容错；安全来源与 Markdown 序列化 |
| `src/papertrail/ui.py` | 查询/采集显式重试控件；图缓存接入；关键词统计表；静态阅读理由标签与文本容错 |
| `src/papertrail/api.py` | 仅标题请求将连字符规范化为空格 |
| `tests/test_ui.py` | 新增 36 项，原 41 项保留；原采集测试只适配新的按钮身份查找，保留业务断言 |
| `scripts/benchmark_ui_graph.py`（新增） | 固定合成图、预热、多次计时、记录版本和被测函数源码/摘要；无网络 |
| `README.md` | 新统计/重试流程、性能结果、测试命令及限制 |
| 本文件 | 修复设计、性能证据、离线/浏览器证据与剩余人工步骤 |

1. **M1：一次设置箭头。** `network_figure` 收集全部 annotations 后一次 `update_layout`；
   自环 shapes 同样批量设置。箭头方向、端点比例、节点颜色/形状、customdata 与悬停信息保持。
   `cached_network_figure` 在已打开 view 内缓存一张图，键包含数据库/run 身份与节点上限。
   无关控件复用布局，改变上限重新计算，重新打开样本建立新 view；不使用跨用户全局缓存。
   100/200 节点结构性测试禁止 `add_annotation`、断言一次批量布局及边方向；另测自环、
   缓存隔离/重新打开和实际 AppTest 无关控件交互只构图一次。没有易受机器影响的 CI 耗时阈值。
2. **M2：关键词统计。** 新输入使用中英文逗号分隔，调用原 `sample_statistics`，传入完整
   当前 run（含种子）的 rows。显示标题、摘要、标题或摘要三种论文数；并集去重，忽略大小写，
   字面子串匹配。表旁明确“仅代表当前采集样本，不代表整个研究领域”。测试包括缺失字段、
   大小写/重复词、并集，以及候选筛成空、图限制为 3 时统计仍覆盖全部 4 篇。
3. **M3：会话内提交保护。** `operation_state` 为每组参数保留状态与 attempt；提交前先由
   ready 变为 running，终态为 succeeded/failed。旧 attempt、运行中/终态事件在 action 前拒绝。
   查询以原始输入识别；采集包含原始输入、确认结果、规范化绝对 DB 路径及两种预算。
   输入改回旧值也不会清除已完成记录。显式“准备重新…／重试”只增加 attempt、恢复 ready；
   用户须再次点击提交，查询重试后须重新确认。失败/中断保持失败，不当作成功；中断异常不吞掉。
   自动化覆盖运行中重入、旧尝试、改参数再改回、查询失败、采集失败/中断、成功后重试及脱敏。
   这是单个 Streamlit session 的状态保护；不实现跨会话锁，也未声称验证真实浏览器并发排队。
4. **M4：连字符标题。** 只对 `/paper/search` 请求 query 替换 ASCII 连字符和常见 Unicode
   横线为空格并合并空白。原输入展示、人为确认、DOI/arXiv 请求路径不变。mock 参数化验证
   `Pre-training` 等三类输入及标识符不受影响，未调用真实检索端点。
5. **M5：展示容错。** `display_text`、`author_names` 和 `source_link` 在边界检查字段类型，
   忽略 None/缺字段作者，容忍 null/非 dict externalIds，缺标题/摘要给出合理缺失提示。
   测试包括 6 种 externalIds 情况、缺失元数据、实际控件展示；不更改数据采集业务规则。
6. **M6：Markdown 与标签。** 导出不可信单元格为字面代码片段，动态选择足够长的反引号
   围栏，表格分隔符在片段外转义，换行由固定 `<br>` 保留。来源栏单独生成 HTTP(S) 链接，
   拒绝危险协议、凭据、控制字符和畸形 URL；这是格式/协议验证，未联网确认论文真实性。
   用户阅读理由标签改为固定编号，论文标题通过纯文本另行显示。测试包含 10 种不安全 URL、
   图片/恶意链接/HTML/裸 URL、中文、引号、反引号、反斜线加竖线及换行；CSV 原语义保留。

可选 **M7 暂缓**：下拉更新详情不承诺同步清除 Plotly 旧高亮。M8、跨会话清单持久化、
复杂缓存与其他 Deferred 未处理，没有新增产品页面。

### 性能实测

同机 Python 3.11.7 / NetworkX 3.6.1 / Plotly 6.9.0，固定三种子合成图；预热 6 节点，
每种规模各测 3 次。范围为 `spring_layout + Plotly figure` 构造，不含浏览器绘制/传输，
不是浏览器帧率或所有图密度的保证。

| 节点/边 | 修改前三次（秒） | 修改后三次（秒） | 中位数前 → 后（秒） |
|---|---|---|---|
| 100 / 293 | 25.355417、27.547419、27.710210 | 0.087125、0.087826、0.094003 | 27.547419 → 0.087826 |
| 200 / 593 | 118.280769、119.665214、124.311646 | 0.281660、0.277449、0.271412 | 119.665214 → 0.277449 |

中位数约快 314 倍/431 倍。原始证据在 Git 忽略的 `output/task0041-performance/`：
`before.json`、`before.function.txt`、`before-clean100.json`、`after.json`、`after.function.txt`。
首次 100 节点计时与基线 pytest 重叠，因此未用于表格；待 pytest 结束后，执行保存的修改前
函数源码重新测得 `before-clean100.json`。200 节点修改前及修改后均没有并行 pytest。
脚本记录函数 SHA-256 与源码，便于检查被测版本；当前源码只复现修改后测量。

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_ui_graph.py --label after --repeats 3 --output output/task0041-performance/after.json
```

### 回归结果与验证边界

```powershell
# 修改前基线：319 passed in 75.99s
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task0041-pytest-cache

# UI 定向：77 passed in 24.62s
.\.venv\Scripts\python.exe -m pytest tests/test_ui.py -q -o cache_dir=output/task0041-pytest-cache

# 最终完整回归：355 passed in 85.76s (0:01:25)
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task0041-pytest-cache

git diff --check
# 通过；原有 safe.directory / LF-CRLF 提示保留，未修改用户配置
```

新增测试首次为 69 passed / 8 failed，均定位到测试设置：4 项试图让 AppTest 点击禁用按钮，
3 项错误地把 URL 编码后的 ID 路径与未编码文本直接比较，1 项在控件渲染后替换其选项元数据。
修正为检查按钮禁用并直接验证同一会话状态拒绝旧事件、解码路径后比较、渲染前注入异常元数据，
随后 77/355 项通过。AppTest 不支持向已禁用按钮注入点击，不能把这些测试称为浏览器并发实测。

本机浏览器仅访问 localhost，使用合成 fixture：

- 重启本机旧服务后打开新版演示，实际在“样本统计”输入 `synthetic, example`；
  表格显示 4/0/4 与 0/1/1，明确完整采集样本声明。截图
  `output/task0041-evidence/keyword-statistics.png`。
- 使用 Git 忽略目录中的临时 Markdown 渲染探针，专门允许 HTML 以检查导出转义效果；
  **产品没有开启 unsafe HTML**。包含图片语法、javascript 链接、HTML img、裸 URL 和特殊符号的
  单元格实际渲染为字面文本。DOM 检查为 **0 张图片、仅 1 个来源链接、10 个表格单元格、
  2 个换行**；中文、引号和反斜线竖线保留。没有点击来源链接或调用论文 API。
  输入与截图分别为 `output/task0041-evidence/rendered-input.md`、`markdown-render.png`。
- 临时探针服务已停止；新版主服务保留在 `127.0.0.1:8501`，供 Owner 验收。
  初次全页探针截图失败，改为成功保存视口截图；未将失败截图当作证据。
  最终 Windows 端口枚举因权限被拒绝，改用本机 `/_stcore/health` 检查，返回 `ok`。
  浏览器连接关闭时服务记录一次 WinError 10054；进程仍运行，随后健康检查通过。

### 仍需人工验收

1. 在 mock 传输环境（或另行 Owner 明确授权的真实测试）快速连续点击查询/采集按钮，
   检查只出现一次操作与一个 run；运行中及完成后切页、改筛选、排队点击不得出现第二轮请求。
2. 同样环境下模拟失败/中断，确认仍显示失败；点“准备重试”不得请求，再提交才执行一次。
   查询重试必须重新确认。修改 DB/预算/确认结果后核对状态，改回已完成参数仍须显式准备重试。
   这些步骤的状态机制已自动化验证，但真实浏览器事件并发/排队尚未实测。
3. 用已保存的较大真实 run 或合成样本，切换 100/200 上限，检查箭头重叠、缩放、点击详情
   与连续控件操作流畅度。构图计时与结构性测试通过不等于大图浏览器交互通过。
4. 目标 Chrome/Edge 及 Excel/WPS/Markdown 阅读器兼容性仍需课堂环境核对；真实论文搜索、
   DOI/arXiv、限流与来源链接目标均未在本轮联网验证。

建议阅读 `ui_service.py::execute_operation/prepare_retry`、`cached_network_figure`，以及
`ui.py::statistics_panel`。自查问题：为什么“准备重试”只改变状态，而不能直接调用采集？
应能解释旧点击事件与新的主动尝试需要分开，以及失败结果不能因重绘变成成功。

最终工作区保留全部未提交 TASK-004 文件，并新增性能脚本；无 Commit/Push。
完成此记录与差异检查后停止修改，等待 Owner 最终验收及独立只读 Review。
