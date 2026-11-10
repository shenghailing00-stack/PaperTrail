# TASK-002B — 引用网络构建与基础分析

**状态：** TASK-002B 及 TASK-002B.1 限定修复与离线验证完成，等待 Owner 最终验收及 Claude Code 独立只读审查。  
**日期：** 2026-11-10（America/Los_Angeles）。  
**主要实现者：** Codex，唯一编辑者，未启动子代理。  
**边界：** 仅图分析及 CLI，无 UI、PageRank、聚类或复杂评分，无真实 API、Commit/Push。

## 开发前检查与方案

读取 AGENTS、PROJECT_BRIEF、README、src/papertrail 全部现有模块和 TASK-002A.1 记录。
`git status --short` 与 `git ls-files -u` 均为空；HEAD 为
`1ab8244 feat: add traceable collection runs and request safety`，上一任务已提交。
任务旧记录中的“未提交”是历史状态，以本次 Git 检查为准。未修改已有 Git 配置警告。
基线运行原测试：134 passed in 10.97s。

复用 `PaperStore.open_readonly()` 和 `get_sample(run_id)`，没有架构冲突。
先声明修改文件、严格拒绝异常样本和保留自环的策略，再实施；不修改采集规则或存储 schema。
验收覆盖方向/度数、不同 run 与历史隔离、去重、孤立节点、自环、无效输入及只读性。

## 文件与接口

| 文件 | 变更 |
|---|---|
| `src/papertrail/analysis.py`（新增） | 样本有效性检查、只读加载 DiGraph、统计及候选 JSON |
| `src/papertrail/analysis_cli.py`（新增） | `--db/--run-id/--top/--json`，摘要、错误和脱敏 |
| `scripts/analyze.py`（新增） | 直接从仓库运行的入口 |
| `tests/test_analysis.py`（新增） | 初版 58 项，TASK-002B.1 后 77 项离线测试，临时 DB 和合成 fixture |
| `requirements.txt` | 增加 `networkx>=3.4,<4` |
| `README.md` | 分析命令、指标语义、限制和测试证据 |
| 本文件（新增） | 任务方案、验收、取舍和交接 |

未修改 api.py、store.py、collector.py、原 cli.py、既有测试或 fixture 文件。
Python 3.11.7，现有 `.venv` 安装 NetworkX 3.6.1。首次 pip 请求被受限网络环境阻止
（WinError 10013）；在 Owner 已授权安装范围内重试成功。联网仅用于这次依赖安装，
未核验外部 API 文档或调用真实 API。使用已安装 NetworkX 的 API/docstring 确认图操作。

## 数据结构、校验与指标

调用路径：`scripts/analyze.py → analysis_cli.main → load_graph(db_path, run_id)`，
后者通过只读连接读取 `get_sample(run_id)`，校验后先加所有节点、再加有向边。
边为 `citing → cited`；重复有序端点由 DiGraph 去重，孤立种子保留。
不读取历史 citation_edges，也不对历史边做“两个端点都在当前样本中”的筛选。

- 节点：paperId、title、year、url、authors、externalIds、abstract、reference_count、
  global_citation_count、source、updated_at、metadata_fetched_at、is_seed；缺失值保留 None/null。
- 图属性：run_id、来源及 fixture 指纹、状态、采集时间、配置、种子、原始计数、覆盖和警告。
- 指标：节点数、唯一边数、`in_degree`、`out_degree`、`number_weakly_connected_components`、
  孤立节点数、自环数。弱连通分量忽略边方向，孤立节点独占一个分量。
- 自环保留且单列：每条增加 1 条边、1 入度和 1 出度，不改变弱连通分量。重复自环只算一条。
- 候选：正局部入度降序、paperId 升序处理平局，包含种子；给出观察到的唯一入边数。
  不按全局引用量二次排序，不把候选称为质量、经典或 baseline 分类。

按 TASK-002B.1 的 Owner 决策，接受有结束时间、报告且 run/report 状态一致的 `ok` 或
`bounded` run；核对报告身份、来源、输入种子、两个方向的
complete/truncated/error/游标、实际原始计数、配额、请求预算和样本总数。
`bounded` 可分析并提示截断：至少一个方向按配额主动停止，截断原始数量须恰好等于
保存上限和本次 limit。它不是采集失败，也不能称为完整文献网络。`ok` 不得有截断方向。
`incomplete/interrupted/running/error` 或未知状态、partial/skipped/error 方向仍报错。
请求恰好用完但各方向有效结束可以分析；请求预算耗尽后报错和超预算不可以。
完整缓存保留旧 target_limit 属于现有行为：实际行数须同时符合旧上限和本次配额，
不强制旧上限等于新配额，以兼容已完整缓存的预算变化。

边端点必须都属于当前样本，样本 ID 必须有效，论文行必须存在且来源一致。
缺 ID 的原始行不转换为节点/边；未解析数、缺摘要数、count_mismatch 仍明确显示。
这类已到末页的质量缺失不等于采集流程失败，允许分析已解析部分，但不能声称文献覆盖完整。
空节点集报错；仅含无边种子的样本可分析。真实/fixture 标签按保存身份判断，无法鉴定
外部把合成数据和全部来源字段一起伪造成真实来源的情况。

`summarize_graph(graph, top=5)` 返回 JSON 兼容字典：
`sample/statistics/nodes/edges/candidates/candidate_order`。节点表含 local_in_degree 和
local_out_degree；global_citation_count 单独命名。后续界面直接复用此接口，应一并展示
warnings/limitations，且使用 load_graph 返回的未修改图，避免图变动后证据与统计不一致。

节点元数据是**同一读取事务中的最新已知值**，不是 run 当时的属性快照；旧 run 的成员保持，
其标题/全局引用数可随后更新。WAL 测试确认成员与元数据不会混入读取事务之后的新提交。

## TASK-002B 初版验收证据

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002b-pytest-cache
# 192 passed in 19.29s = 原有 134 + 新增 58
git diff --check
# 通过；已有 safe.directory / LF-CRLF 提示未更改配置
```

本次只使用上述 Python pytest 入口；未另外重跑 pytest.exe 同义入口。
首轮新增测试为 54 passed / 2 failed：fixture 缺摘要原始记录应为 6 而非 5；只读测试拦截器
首参数名 uri 与 SQLite 的 uri=True 冲突。中间一轮为 57 passed / 1 failed，参数名修正后
完整测试通过。测试中的主动 UPDATE 被拒绝后显式结束隐式事务，再测试正式读取事务。
诊断用临时脚本退出时还遇到 SQLite 连接留存导致 WinError 32 临时目录清理失败；
未手动删除文件，该失败不是分析写库或业务测试通过的证据。

| 验收点 | 实际测试 |
|---|---|
| 方向、入出度、字段和未解析行 | `test_direction_degrees_metadata_and_coverage` |
| 不同 run / 相同节点中的历史多余边 | `test_runs_do_not_induce_historical_edges_even_between_current_members` |
| 重复和自环 | `test_duplicate_observations_and_self_loops_are_counted_once` |
| 孤立种子与弱连通分量 | `test_isolated_seeds_and_weak_components` |
| 最新元数据、全局引用不影响排名 | `test_metadata_is_latest_known_and_global_count_does_not_rank` |
| 缓存预算兼容、真实采集器截断/失败 | `test_completed_cache_with_changed_budgets_is_accepted` / `test_real_collector_budget_truncation_and_failure`（002B.1 更新截断断言） |
| 异常状态、报告、预算、来源、端点 | invalid_or_inconsistent_run_status / inconsistent_report / inconsistent_database / membership_contract 参数化组 |
| 空样本、缺失 run、异常数据库 | empty_stored_membership / missing_run / unusable_databases 测试 |
| 真正只读、无历史边查询 | 禁用普通构造；强制 mode=ro；授权器禁止写操作及读历史边；UPDATE 被拒绝；文件字节与 iterdump 不变 |
| 同一读取快照 | `test_wal_metadata_and_membership_share_one_read_snapshot` |
| CLI | 摘要/完整 JSON、独立进程入口、错误退出码、敏感数据脱敏、无效参数打开 DB 前拒绝 |

“真实来源”测试只人工设置来源标签验证显示逻辑，仍全为合成数据；不等于真实 API 验证。

## 实际离线演示

本地证据位于 Git 忽略目录 `output/task002b-demo/`：`sample.db`、`collection.json`、
`summary.txt`、`analysis.json`。未触碰用户既有数据库。实际调用如下（新库 run_id=1）：

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db output/task002b-demo/sample.db --page-size 2
.\.venv\Scripts\python.exe scripts/analyze.py --db output/task002b-demo/sample.db --run-id 1 --top 3
.\.venv\Scripts\python.exe scripts/analyze.py --db output/task002b-demo/sample.db --run-id 1 --top 3 --json
```

首次采集和分析均退出 0；报告来源为 Synthetic offline fixture。再次复现采集请使用实际输出的
新 run_id。实际 4 节点 / 5 边 / 1 弱连通分量 / 0 孤立 / 0 自环；7 原始记录、1 未解析、
1 次重复边观察、6 条缺摘要。JSON 分析前后数据库字节不变。

| 论文（ID 首字母简写） | 入度 | 出度 | 全局引用字段（合成） |
|---|---:|---:|---:|
| A / Synthetic seed A | 2 | 1 | 2 |
| B / Synthetic seed B | 1 | 2 | 1 |
| C / Shared synthetic reference | 2 | 0 | null |
| D / Synthetic citing paper | 0 | 2 | null |

候选顺序 A、C、B；边示例 A→C、B→A、D→B，全部 5 条边保存在 JSON 中。

## 取舍、学习与后续

1. 按预算主动停止的 bounded 样本允许分析并标明范围；失败/中断样本仍拒绝，不将截断样本表述为完整文献网络。
2. 保留自环和独立计数，保留原始观察证据；代价是度数可能包含自引，提示必须随图展示。
3. 使用最新已知元数据和同一只读事务，避免增加历史属性版本系统；旧 run 属性不可复现为历史值。
4. 仅按局部入度提供可解释候选，固定 ID 处理平局，不加入额外评分或网络请求。

重点阅读三处：`analysis.py::_validate_sample`（异常拒绝与缺失提示）、
`analysis.py::load_graph`（get_sample、只读事务、先节点后边）、
`analysis.py::summarize_graph`（指标与候选）。手工检查：运行上面的摘要与 JSON 命令，
确认 A 入度 2、出度 1，并找到未解析条目的覆盖警告。

本科生理解题：

1. 对 A→C、B→C、B→A、D→A、D→B，分别计算每个节点的入度/出度；为什么所有入度之和
   等于所有出度之和？再加入 C→C，哪些值改变？
2. 删除方向后全图连通，是否代表任意两篇论文沿有向边均能相互到达？增加一个无边种子 E
   后弱连通分量数怎样变化？为什么不能把历史表中额外的边补进当前 run？

后续接口需求：UI 接收现有 JSON/DiGraph 和用户选定 run_id，必须保留来源、覆盖和自环提示；
若未来支持失败/中断样本、筛选后的指标或历史属性快照，需另定输出语义和验收范围。
真实 API 分页/DOI 与实际论文有效性验证仍未执行；单层样本缺候选间引用，不能外推为全领域。
本模块的一致性检查不是签名或完整数据库防篡改审计，不修复被外部改动的记录。
不继续开发后续功能，停止修改，等待 Owner 验收与 Claude Code 独立审查。

## TASK-002B.1 — 预算截断样本分析与 Unicode 输出（2026-11-10）

Owner 明确修订预算截断策略，并将 M2 列为本轮必修。Codex 仍为唯一编辑者，只改
`analysis.py`、`analysis_cli.py`、`tests/test_analysis.py`、`README.md` 和本任务记录。
上一轮未提交的 TASK-002B 文件保持在工作区；本轮不修改 collector/store/原 cli/schema、
依赖或脚本入口，不安装依赖、不联网、不 Commit/Push、不开发 TASK-003。

### 根因与限定修复

- B1：旧 `_validate_sample` 仅接受 ok/complete，因而拒绝设计上正常的预算采样。
  现要求 run.status == report.status 且属于 ok/bounded。complete 保留全部原检查；
  truncated 必须 status=truncated、truncated=1、complete=0，无 error_kind/error_message/
  stop_run，raw_count == target_limit == 本次 limit，允许 next_offset 非空。
  references 的 limit 为 reference_cap，citations 为该种子 citation_quota。
  ok 不得含 truncated，bounded 至少含一个；请求预算失败仍拒绝。
- JSON 的 sample（对应 graph.graph）新增 collection_scope、truncated_directions。
  complete 表示采集方向到达数据源报告末页，budget_truncated 表示预算截断；二者都不是
  完整文献网络。每个截断方向附实际数/上限/expected_count 和 API 顺序采样警告。
- M1/M5：候选增加 is_seed；种子 reason 和文本 `[seed]` 标记解释采样配额影响。
  LIMITATIONS 加入种子入度不能与非种子直接比较的说明。所有方向的 expected_count
  始终进入 sample.direction_states，缺失为 null，不限于 count_mismatch。
- M2：本机 Python 重定向输出编码实测为 gbk（locale cp936）；部分 Unicode 标题无法编码。
  CLI 对 stdout/stderr 保留现有编码，设置 errors=backslashreplace；支持的字符原样显示，
  不支持的显示为反斜杠转义。内存捕获流没有 reconfigure 时无需编码，跳过配置。
  JSON 继续 ensure_ascii=True，解码可还原中文及所有 Unicode；不要求 PYTHONIOENCODING，
  不强改控制台编码。此策略防止 UnicodeEncodeError，不承诺终端字体一定有对应字形。

### 本轮验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_analysis.py -q -o cache_dir=output/task002b1-pytest-cache
# 77 passed in 17.41s
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002b1-pytest-cache
# 211 passed in 28.71s
git diff --check
# 通过
```

本轮定向和完整测试首轮均通过，无失败项。原 192 项覆盖保留，涉及预算截断的旧拒绝断言
按 Owner 新规则改写；新增 19 项（共 77 项图测试 + 原采集/API 的 134 项）。
未执行无关重构、大规模变异测试或额外的同义 pytest 入口。

| 本轮验收 | 证据 |
|---|---|
| 5 篇 citing / 预算 4 / page_size 2 | 真正 collect + FixtureClient 产生 bounded；6 节点、5 边，A 入度 4 / 出度 1，C 入度 1 / 出度 0，B/D/E/F 各出度 1，G 不入图 |
| 截断范围与警告 | truncated_directions 为 A/citations/raw_count=4/target_limit=4/expected_count=5；JSON 和文本均展示 budget_truncated、方向、警告与局限 |
| reference_cap=1 与 citation_budget=0 | 有效截断可分析，分别报告参考方向配额 1 和被引方向配额 0；不是采集失败 |
| request_budget=1 | 真实采集器产生 incomplete，仍拒绝 |
| 伪造状态与数量 | raw_count<target_limit、上限不匹配、错误/stop_run/标记矛盾、ok 含截断、run/report 不一致、bounded 无截断全部拒绝 |
| 候选与预期数 | A/C/B 的 is_seed=True/False/True；种子 reason 和 `[seed]` 标记；完整方向和缺失预期数均保留 expected_count |
| Unicode 子进程重定向 | `中文论文 Schrödinger ő 🧪`，不设置 PYTHONIOENCODING；默认编码、强制 cp936、UTF-8 各测试文本/JSON，共 6 项，退出码均为 0；JSON 原文完整还原 |
| 只读边界 | 新 bounded 分析及 Unicode CLI 测试前后 DB 字节不变；原只读/历史隔离/WAL 测试保持通过 |

### 交接

主要取舍：只放行可验证的预算截断，不放行采集失败；保持原候选排序但明确种子偏差；
文本保留环境编码并转义不支持字符，JSON 用于完整 Unicode 交换。
重点阅读 `_validate_sample` 的双状态校验、`load_graph` 的范围/方向摘要、
`analysis_cli.main` 的编码配置和文本范围输出。
手工检查：按 README 的单种子 citation-budget=1 离线示例生成 run，再分析，确认出现
budget_truncated、对应 citations 截断详情和 `[seed]` 标记，并解释它为什么不是完整网络。
真实 API 验证仍未执行；expected_count 是保存的来源计数，可能为空或过时；元数据仍为
最新已知值，关系仍为单层 API 顺序样本。停止修改，等待 Owner 最终验收。
