# TASK-002A — 一层引用采集与 SQLite 持久化

**日期：** 2026-11-02（America/Los_Angeles）  
**状态：** Claude Code 独立审查结论为“修复后验收”；Owner 批准的修复已实现并通过离线测试，等待复核验收。  
**主要实现者：** Codex（唯一编辑者）；没有启动其他编辑代理。  
**授权边界：** 本地开发、离线测试；未进行真实 API 或其他联网请求，未 Commit / Push。

## 需求与范围

从 1–3 个种子标识符出发，先取得规范化 Semantic Scholar paperId，再获取一层
references 和受全局预算约束的 citations，去重持久化并支持缓存、刷新和分页恢复。
保持 TASK-001 实验脚本与 31 项原有测试可运行。

输入是 S2 paperId 或带前缀 DOI / ARXIV / CorpusId / ACL / MAG / PMID / PMCID / URL。
本任务没有标题检索、Streamlit、排序/PageRank、社区发现、NetworkX 分析、OpenAlex、
递归扩展、多线程或异步爬虫。TASK-002B 未开始。

开始时已读取 `docs/PROJECT_BRIEF.md`、`AGENTS.md`、README、原实验脚本、原测试、
TASK-001 任务记录；工作树初始干净。Python 为现有虚拟环境的 3.11.7，无新增依赖。
先解释模块职责、表结构、预算/缓存设计与验收测试，再修改代码。

## 实际文件与职责

| 类型 | 路径 | 行为 |
|---|---|---|
| 新增 | `src/papertrail/__init__.py` | 最小包入口 |
| 新增 | `src/papertrail/api.py` | 从 spike 提取既有请求/重试/脱敏/ID 逻辑；增加 metadata、relation_page、parse_page |
| 新增 | `src/papertrail/store.py` | SQLite schema、别名解析、upsert、逐页事务、持久化进度 |
| 新增 | `src/papertrail/collector.py` | 规范化和去重种子、分配预算、缓存/续传、一层扩展和报告 |
| 新增 | `src/papertrail/cli.py` | CLI 参数、退出码、纯离线合成 fixture 适配器 |
| 新增 | `scripts/collect.py` | 无需 editable install 的直接脚本入口 |
| 新增 | `tests/test_collection.py` | 当前 61 个离线测试用例，均使用临时数据库；初版为 44 个 |
| 新增 | `tests/fixtures/s2_collection.json` | 两篇合成种子、重复/未解析记录和共享邻居 |
| 修改 | `scripts/api_spike.py` | 导入共用请求逻辑；原单页探测、质量统计和 JSON 输出保留 |
| 修改 | `pytest.ini` | `pythonpath = . src`，兼容两个 pytest 入口 |
| 修改 | `README.md` | 模块、表、运行命令、预算、分页、缓存和局限 |
| 新增 | 本文件 | 需求、实施与离线验收证据 |

没有修改 `tests/test_api_spike.py`、原 fixture、依赖清单、项目简报或 TASK-001 历史记录。
已有 `.gitignore` 覆盖数据库、测试缓存、虚拟环境和 `output/`，无需修改。

## SQLite 结构与写入保证

| 表 | 主键 / 约束 | 保存内容 |
|---|---|---|
| `papers` | `paper_id` 主键，非空白 | title、year、authors_json、abstract、url、external_ids_json、reference_count、citation_count、source、updated_at、metadata_fetched_at |
| `paper_aliases` | `alias` 主键；paper_id 外键 | 输入及附带外部 ID 映射、confirmed、conflicted |
| `citation_edges` | `(citing_paper_id, cited_paper_id)` 联合主键，双方外键 | source、fetched_at |
| `fetch_state` | `(seed_id, direction)` 联合主键，种子外键 | status、next_offset、raw_count、unresolved_count、missing_abstract_count、complete、truncated、target_limit、error_kind、error_message、expected_count、warning_kind、warning_message、started_at、updated_at |

- 所有边固定 `citing -> cited`：references 为种子到参考论文；citations 为来源论文到种子。
- SQLite 外键开启；重复论文/边通过 upsert 去重；缺 ID 不存论文、不造边，但计入原始记录和未解析数量。
- 每个关系页与该页 checkpoint 同事务提交。后页 HTTP 失败不回滚前页；SQL 写入失败回滚整页。
- 可空字段不要求填充伪值；null、空字符串/纯空白、空列表、空字典不擦除已知值，0 保留有效语义；作者和外部 ID 使用 JSON 文本。
- `metadata_fetched_at` 只在直接取得种子元数据时更新。候选记录更新时间不冒充有效种子缓存。
- 别名统一输入前缀和 DOI 大小写。直接确认且无冲突的别名才可命中种子缓存；附带 ID
  只作线索且不能覆盖冲突映射。查询后的 canonical paperId 决定去重，两个不同 paperId
  不因附带 ID 相同而合并。元数据失败且无 paperId 时只记录在报告中。
- 数据库保留历史成功观察的并集。刷新、缩减预算不会删除已有边，累计数据库不能当成本轮快照。

## 分页、配额、缓存与错误语义

1. 先解析所有种子，按 canonical paperId 去重，保持首次输入顺序。
2. references 默认每篇安全上限 10,000 条原始记录；citations 默认全局 150，
   对成功解析且去重后的种子均分，余数按顺序分配；未用配额不转移。
   两篇各 75、三篇各 50，151 / 三篇为 51、50、50。
3. 默认 page_size=100（1..1000）；每页 limit 不超过剩余配额。
   重复和 unresolved 记录都占预算。只查询种子关系，不递归补查候选元数据或候选关系。
4. 每页检查 data、offset、next。next 缺失才是 API 报告末页；next=null、非连续/不前进
   的偏移、空页带 next 等均明确报错，保留可用记录，不猜游标。可靠末页计数与元数据
   不等仍标记 complete，并保留 count_mismatch 质量警告，不影响缓存或 CLI 成功退出。
   超 limit 响应保留真实数量并停止整轮，不能声称严格预算成功。
5. 缓存 TTL 默认 7 天。直接元数据依据 metadata_fetched_at，关系成功缓存及续传依据 started_at；
   updated_at 不延长有效期。
   完整结果可在其原始数量不超过当前配额时复用；相同 target_limit 的截断结果可复用，
   但继续标记 truncated，不变成 complete。
6. 错误/部分状态不作为完整缓存。started_at 仍在 TTL 内且有可靠 next_offset、配额未缩小
   时续传；已缓存/保存的前缀也占当前配额。增大配额可续传，缩小配额时部分/截断状态
   从 0 重取，完整缓存仅在 raw_count 不超过新配额时可复用。过期或 `--refresh` 重置
   该方向进度至 0，历史论文/边保留。TTL=0 禁用跨运行复用。
7. 当时继承 1.1 秒起始间隔（TASK-005.1 当前为 2.0 秒）、5/20 秒 timeout、每页最多 3 次尝试、2/4 秒退避、Retry-After
   上限 10 秒、匿名第二次 429 停止、证书错误不重试、明确的暂时性 SSL 中断有限重试。
   认证/限流/耗尽重试的连接故障停止后续请求；不关闭 TLS 校验，不输出底层敏感异常文本。
8. `raw_count` 是该方向当前采集轮次（含缓存/续传前缀）的原始记录数；
   `network_raw_records` 是本次调用的新返回记录，`network_citation_records` 汇总本轮被引记录。
   `database_totals` 是数据库累计唯一论文/边；这三个统计口径明确分开。
9. 退出码 0 包含 `ok` 和 `bounded`，后者是明确截断；`incomplete` / 本地错误退出 1，
   CLI 参数错误退出 2。单独缺失种子不妨碍其他有效种子的采集，致命网络故障除外。

## 运行与验收证据

完整安装/运行示例见 [README](../README.md)，以下命令在仓库根目录执行。
使用已有 `.venv`，没有安装依赖。没有把正式用户数据库用于测试。

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002a-pytest-cache
.\.venv\Scripts\pytest.exe -q -o cache_dir=output/task002a-pytest-cache
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/collect.py --help
.\.venv\Scripts\python.exe scripts/api_spike.py --help
git diff --check
```

| 验证 | 实际结果 |
|---|---|
| 修改前原有测试 | 31 passed in 1.23s；旧 pytest 缓存目录不可写，出现 1 个 warning |
| 提取请求层后回归 | 31 passed in 0.73s，改用新的忽略缓存目录，无上述 warning |
| 初版完整测试，python -m pytest | **75 passed in 3.07s**；独立审查后结果见下方修复记录 |
| 初版完整测试，pytest.exe | **75 passed in 2.87s** |
| pip check | No broken requirements found. |
| 两个脚本 --help | 均 exit 0，无网络请求 |
| git diff --check | 通过；已有全局 safe.directory / LF-CRLF 提示未改配置 |

另用 Python `TemporaryDirectory` 在忽略的 output 下创建临时 DB，通过 subprocess
实际执行下列采集命令两次；测试结束关闭并清理临时目录（未创建正式用户 DB）：

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db <临时目录>/demo.db --page-size 2
```

| 执行 | 状态 / 模式 | 唯一论文 / 边 | 本次新返回被引记录 | 方向缓存命中 |
|---|---|---|---|---|
| 第一次 | ok / offline_fixture | 4 / 5 | 3 | 0 / 4 |
| 第二次（重新启动进程） | ok / offline_fixture | 4 / 5 | 0 | 4 / 4 |

离线 CLI 不构造 HTTP 客户端、不读取环境密钥。fixture 缺失条目直接报告失败，不联网补齐。
保存来源注明 Synthetic offline fixture；不将合成结果描述为真实 Semantic Scholar 覆盖。

### 验收覆盖

- [x] 单篇、多篇以及三篇预算分配；1–3 篇输入校验。
- [x] 两种引用方向、分页合并、只扩展种子。
- [x] 全局 150 条原始记录预算；重复和缺 ID 记录计入预算；不均分余数。
- [x] canonical ID、已知/未知外部别名、重复边、null 字段处理。
- [x] 完整缓存与有界截断缓存、TTL、显式刷新、扩大/缩小配额。
- [x] references 安全上限、空响应、非法 next/offset、计数不符、超 limit。
- [x] 429、SSL 证书/EOF、503 的部分保留，关闭重开后断点恢复。
- [x] 一个种子命中缓存、另一种子续传时仍共用全局配额。
- [x] 刷新失败保留旧数据且失败方向不冒充完整缓存。
- [x] SQL 人为失败触发整页回滚，不提前推进 checkpoint。
- [x] CLI 离线演示、错误退出、重新启动数据库持久存在。
- [x] TASK-001 原有 31 项测试不改动并全部通过。
- [ ] 真实 API 分页、真实缓存再访问：未获授权、未执行，不作为本轮离线验收已通过项。
- [x] Claude Code 初次独立只读审查：Owner 转述结论为“修复后验收”。
- [ ] Owner 学习验收及本轮修复复核。

## 设计取舍与局限

- **复用而非重写：** 已验证 HTTP 算法迁入 api，原脚本保持实验用途；新 CLI 仅做薄入口。
- **可解释的预算：** 固定均分避免前一颗种子独占配额；代价是少引用种子的剩余额度不转移。
- **可恢复的简单事务：** 每页提交，以磁盘 checkpoint 为准；不引入后台任务、快照代次或复杂任务表。
- **累计存储：** upsert/null 保留和历史边并集保护已有成果；代价是不能把整个 DB 解释为最近一次样本。

API 分页顺序不代表重要性，且 offset 不是稳定快照，运行期间或断点恢复时可能发生记录
重排/漏采；主键只解决重复，不补偿这种遗漏。参考文献“完整”仅指 API 报告末页，
不能保证现实参考文献全集。只采种子一层会缺失候选间关系，不能推导全领域网络结论。
论文及边持久化，逐条 contexts/intents/isInfluential 尚未持久化；本任务不扩展证据分析。

协议沿用 TASK-001 中 2026-10-28 的官方文档核验记录；本轮未重新联网核验当前协议、
条款或额度。真实分页行为、REALM/RAG/DPR 覆盖和另一台 Windows 的复现仍未验证。

开发过程中，普通 shell 写入已有实验脚本遇到 PermissionError，随后用工作区编辑工具
成功完成修改；没有提权或修改系统权限。一次 rg 通配符在 PowerShell 下报路径语法错误，
改用目录加 `-g '*.py'` 后检查成功。这些本地问题未被解释为 API 故障。

## Owner 阅读与手工验收

建议重点阅读三处：

1. `src/papertrail/collector.py::collect`：为什么先解析/去重种子再均分配额，以及如何禁止二层扩展。
2. `src/papertrail/collector.py::collect_direction`：缓存命中、断点恢复、失败、完成与截断的区别。
3. `src/papertrail/store.py::save_page`：为什么论文、边、分页进度需要同一个事务。

手工检查：照 README 连续运行两次合成演示，确认第二次 4 个方向 cache_hit=true、
database_totals 始终为 4 / 5；选一条 citations 边解释为什么来源论文指向种子。

两道理解题：

1. **SQL：** 如果 citation_edges 不设联合主键，重复分页会出现什么问题？如果先提交
   next_offset 再写边，进程在两次提交之间退出，会导致什么不可恢复的遗漏？
2. **数据结构：** 为什么种子去重要用以 paperId 为键的字典，而被引预算要按原始列表条数
   计算？两颗种子各返回 75 条，其中存在重复和缺 ID，唯一新增论文数为什么可能远小于 150？

下一步仅为 Owner 验收、Reviewer 只读审查和后续授权；不自动提交、推送或开始 TASK-002B。

## 独立审查后的限定修复（2026-11-02）

Owner 转述 Claude Code 独立审查结论为“修复后验收”，明确批准本节范围。
Codex 仍为唯一实现者，Claude Code 只读审查；本轮无网络请求、依赖安装、Commit、Push，
未开始 TASK-002B。以下内容取代初版涉及 count_mismatch、别名、TTL 和空值的旧行为。

### 实际修改文件与根因

本轮只改 5 个文件：`src/papertrail/store.py`、`src/papertrail/collector.py`、
`tests/test_collection.py`、`README.md`、本任务记录。CLI 逻辑经测试无需修改。

| 修复点 | 原因 | 实际修复 |
|---|---|---|
| count_mismatch | 把已到可靠末页的计数差异当作错误，重复采集 | complete 与质量警告分离；少于/多于均保存 expected_count、raw_count、warning_kind/message；允许缓存且 CLI 返回 0 |
| 别名冲突 | 所有别名都无条件覆盖，附带 externalIds 可直接参与输入去重 | 附带 ID 只作线索，冲突保留已有映射并标记；只有直接查询确认的输入可按响应修改映射 |
| TTL | 完成缓存以最近 updated_at 计算，续传延长旧页寿命 | 关系缓存与续传统一以 started_at 为准；updated_at 仅记录状态更新时间 |
| 空值覆盖 | COALESCE 仅过滤 SQL NULL，空集合/空字符串仍可擦除有效值 | 写入前将 null、空字符串/空白、空列表/字典转为缺失，保留有效数值 0 |
| 配额缩减 | 部分前缀小于新配额时仍续传，与缩减配额重取策略不符 | 部分/截断状态要求新 target_limit 不小于旧值才能续传；缩减时从 0，页 limit 受新额度限制 |

只有正常响应、分页协议校验通过且没有 next，才可标为 complete；请求失败、有下一页、
空页重复游标或其他分页异常仍不能完成。count_mismatch 不写入 error_kind、不影响总状态。

### 别名与兼容策略

- `paper_aliases.confirmed` 表示直接输入查询已确认；canonical paperId 本身也可可信定位。
  `conflicted` 标记附带 ID 与已有映射不一致。未经确认或有冲突的输入强制直接查询，
  即使其当前映射对应的论文刚刚采集过，也不得据此静默合并种子。
- 附带 externalIds 冲突不改已有 paper_id，报告 `alias_conflicts` 列出别名和保留的 ID。
  这是冲突告知，不伪装成“已经解决”。直接查询失败时保留原映射及冲突状态。
- 直接元数据查询成功后，仅请求的输入别名可以确认/重新映射，并清除该别名的冲突状态；
  变化通过输入报告中的 `alias_reassigned_from` 告知。两个 paperId 的论文/边不合并、不删除。
  canonical ID 请求返回不同 paperId 时报告 canonical_id_conflict，不写入错误映射。
- `--refresh` 不跳过不同外部输入别名的确认；完全相同的重复输入可复用本轮已确认结果。
- SQLite 增量添加 confirmed/conflicted、expected_count、warning_kind/message。
  老外部别名无可证明的直接确认来源，默认未确认，首次输入时重查；canonical 自身保留可信。
  老 count_mismatch 错误不自动晋升 complete，下一次成功采集后使用新语义。
  迁移不删除原数据，重开重复迁移不会清除已确认状态。

### 每项测试证据

所有测试使用临时数据库、合成数据、mock HTTP；正 TTL 测试使用可控墙钟，无真实等待。

| 验收点 | 测试与断言 |
|---|---|
| 预期 40、实际 39 | `test_count_mismatch_is_complete_cached_and_cli_success`：两个方向分页后 complete，警告持久化，CLI 0；重开 DB 后第二次无元数据/采集请求 |
| 返回超过计数 | 同一测试参数 actual=41；仍是质量警告，不视作请求失败 |
| 仍有 next / 请求失败 / 分页异常 | 既有全局预算、后页故障、非法分页测试继续通过，状态不为 complete |
| 附带别名不得覆盖 | `test_external_alias_conflict_retains_mapping_and_requires_direct_confirmation`：保留旧映射、报告冲突、直接确认后显式重映射，两篇论文仍存在 |
| 不静默合并种子 | `test_unconfirmed_external_alias_cannot_merge_distinct_input_seeds`：A 附带 B 输入的错误别名时仍直接查询两篇，保留 A/B 两颗种子 |
| 刷新与 canonical 保护 | `test_refresh_does_not_skip_a_previously_confirmed_alias`、`test_canonical_id_cannot_be_reassigned` |
| 续传不得重置 TTL | `test_positive_ttl_resume_does_not_rejuvenate_old_pages`：TTL=10 秒，第 9 秒续传为 complete/truncated，第 11 秒均重新从 0 请求 |
| 过期不续传 | `test_expired_checkpoint_restarts_instead_of_resuming`：达到 TTL 边界时丢弃旧游标，从 0 采集 |
| 空值保护 | `test_empty_metadata_does_not_overwrite_known_values`：null、空串、纯空白、空列表、空字典覆盖论文所有元数据字段；另确认计数 0 可正常更新 |
| 缩小预算 | 原有 complete 缓存缩减测试继续通过；`test_smaller_budget_restarts_partial_prefix_even_when_it_fits`：旧配额 10/前缀 2，新配额 3，重新请求 offset=0 limit=2、offset=2 limit=1 |
| 空页重复游标 | `test_malformed_pages_never_cached_as_complete` 新增 data=[]、next=0，拒绝完成且有界结束 |
| 输出密钥保护 | `test_cli_report_redacts_key_in_inputs_errors_and_alias_conflicts`：合成 Key 出现在输入、响应外部 ID、SSL 异常中，CLI JSON 无原值，新增冲突字段同样脱敏 |
| 老库兼容 | `test_legacy_database_migration_preserves_data_and_reconfirms_external_alias`：旧字段结构迁移、原数据保留、外部别名重新确认、再次打开不丢确认 |

执行命令与结果：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002a-pytest-cache
# 92 passed in 4.76s
.\.venv\Scripts\pytest.exe -q -o cache_dir=output/task002a-pytest-cache
# 92 passed in 7.14s
git diff --check
# 通过
git status --short
git diff --cached --name-only
# 暂存区无变更
```

原有 TASK-001 的 31 项测试未改且全部通过；采集测试现为 61 项，总计 92。
初版不符合新要求的 count_mismatch 测试已替换；原缓存别名测试先直接确认 DOI 再验证
重开命中缓存，不再依赖未经确认的附带 ID。已有 Git safe.directory 和换行提示未修改。

工作区相对 HEAD 仍显示：README.md、pytest.ini、scripts/api_spike.py 已跟踪且修改；
scripts/collect.py、src/、本任务记录、新采集 fixture 和测试未跟踪。pytest.ini、原实验脚本
等是进入本轮前已有的 TASK-002A 改动，本轮未再次修改；无暂存、Commit 或 Push。

Owner 重点复核 `collector.py::collect_direction` 的完成/警告/TTL 分支、
`collector.py::collect` 的可信别名判断、`store.py::upsert_paper` 的冲突与空值处理。
简单手工检查：运行 README 的两次离线演示，第二次仍应缓存命中；阅读回归测试解释
为什么“40 与 39 不一致”是质量告警，而“空页 next=0”必须是分页失败。

## 后续工作（状态更新见 TASK-002A.1）

- [x] **TASK-002A.1 已实现并离线验证：** CLI 参数边界和本轮请求尝试总预算（含重试）。
- [x] **TASK-002A.1 已实现并离线验证：** 数据库来源身份绑定，真实/fixture 及不同 fixture 内容禁止混用；旧未知来源不自动认领。
- [x] **TASK-002A.1 已实现并离线验证：** D1 样本成员归属；独立 run、种子和逐条记录，从 run 重建样本而非使用累计边表。
- [ ] 真实 API 分页及 DOI 路径验证：需 Owner 另行授权，离线通过不能替代真实验证。
- [ ] 其他不影响当前任务的 Minor：按独立审查清单另行处理，本轮未宣称修复。

TASK-002A 的离线功能已由 Owner 验收。上述三项后续工作的实现与证据见
[TASK-002A.1](TASK-002A.1-SAFETY-SAMPLES.md)，该新任务仍待独立审查；真实 API 验证和其余 Minor 未标记完成。
