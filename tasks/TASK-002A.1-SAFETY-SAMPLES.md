# TASK-002A.1 — 采集安全与样本归属

**状态：** Claude Code 独立审查结论为“修复后验收”；2026-11-10 最终限定修复与离线验证完成，等待验收。  
**日期：** 2026-11-02（America/Los_Angeles）  
**主要实现者：** Codex，唯一编辑者；未启动其他代理。  
**边界：** 无真实 API 或其他网络请求、无新增依赖、无 Commit/Push、无 NetworkX 分析。

TASK-002A 离线功能已由 Owner 验收。本任务仅补齐 D1 样本成员归属、CLI 请求安全和
模拟数据隔离，不实现 TASK-002B。实施前读取项目简报、AGENTS、现有 schema、采集/请求/
CLI、测试及任务记录，并说明设计和验收标准。

## 问题与最小方案

原 `papers` / `citation_edges` 是历史并集；`fetch_state` 只保存计数和游标，无法知道
哪些边属于某个缓存、哪些原始条目重复或缺 ID。用当前节点集筛选历史边也不正确：
历史边的两端可能都在当前样本中，但该边没有在本次所选方向中被观察到。

采用三张归属表，加一张数据库来源身份表；继续使用原历史表和 fetch_state，不引入
图分析框架、复杂分页对象仓库、后台任务或元数据版本系统。

| 表 / 字段 | 内容与约束 |
|---|---|
| `collection_runs` | run_id 主键；source_key/source、脱敏 inputs_json、config_json、开始/结束 UTC 时间、状态和脱敏 report_json |
| `run_seeds` | `(run_id, seed_id)` 主键，双方外键；去重后的种子与实际 citation_quota |
| `run_items` | `(run_id, seed_id, direction, ordinal)` 主键；other_paper_id 可空、missing_abstract、实际 fetched_at；关联 run_seeds 和有效论文 |
| `dataset_identity` | 单行来源身份；真实为 semantic-scholar，fixture 为 fixture:SHA-256（文件字节） |
| `fetch_state.sample_run_id` | 新增可空外键，指向该方向当前缓存/断点的逐条记录所在 run |

config_json 保存 citation_budget、reference_cap、page_size、ttl、refresh 和 request_budget。
不把种子伪装成关系记录，因此种子数量不会消耗原始记录配额。

## 样本、缓存与事务规则

1. 通过参数和来源校验后，每次 collect 创建独立 run，包括完全缓存命中的调用。
   逐个记录成功解析的种子；确定去重种子数后保存各自配额，再请求关系。
2. 每条返回记录占一个 ordinal，包括重复项、缺 paperId 和畸形条目。
   缺 ID 保存 other_paper_id=NULL；不伪造节点和边，仍占原始预算并保留缺失统计。
3. `get_sample(run_id)` 的节点是该 run 的种子和有效邻居，边只来自该 run 的记录，
   references 为种子 → 邻居，citations 为邻居 → 种子；按有序端点去重。
   不从全库做“端点都在集合内”的诱导边查询。
4. `sample_totals` 是当前样本，`database_totals` 是历史累计；raw_records 包括两方向
   所有记录，和唯一论文/边、网络新返回记录分别计数。
5. 缓存可复用的前提是来源一致、原 TTL/配额规则满足，且能验证逐条记录序号和数量。
   命中时复制记录到新 run，保留原 fetched_at。续传先复制有效前缀，再追加新页。
   新页记录、历史论文/边和 checkpoint 同事务提交；复制前缀和游标变更也同事务提交。
6. 缩减配额或刷新不会改写旧 run。部分/截断状态在缩减配额时从 0 重取；完整缓存的
   raw_count 若已不超过新配额仍可复用。旧历史多余节点/边不会因此进入新样本。
7. HTTP 失败/请求预算耗尽保存 incomplete run，skipped 方向不附加历史缓存。
   SQL 写入失败回滚整页，已提交页保留，run 标记 interrupted；硬终止可能留下 running。
   get_sample 仅展示已提交成员，报告状态必须与成员一同解释，不能声称完整覆盖。
8. run 固定成员和采集配置/统计，**不冻结论文元数据**。papers 中标题、摘要、全局被引数
   等仍是最新已知值；未来分析如果需要历史属性版本，应另定范围。

## 请求与参数保护

| 参数 | 默认值 | 范围 |
|---|---:|---|
| citation-budget | 150 | 0..1000，全局原始被引记录 |
| reference-cap | 10000 | 1..10000，每颗种子原始参考记录 |
| page-size | 100 | 1..1000 |
| request-budget | 100 | 1..300，本轮所有实际请求尝试 |
| cache-ttl-hours | 168 | 0..8760 |

CLI 在打开 DB/创建客户端前验证；collect 库入口同样验证，包括极大整数、非有限 TTL。
`RequestBudget` 由所有端点和重试共享，每次 HTTP 尝试前扣减；缓存命中不计请求。
fixture 采用相同计数协议，但 request_attempts 仅为模拟调用数，不是真实 HTTP。
耗尽后返回 request_budget 错误并停止后续请求，保留成功数据与样本，CLI 退出 1。

保留 TASK-001 限速、timeout、TLS 验证、重试和脱敏；其原单页 CLI 仍有原先的端点
尝试上限。本任务不改变该 CLI 的接口。新请求预算按单次 collect 生效，不是跨进程额度
或严格墙钟时限；大采集目标/小 page-size 可能在达到记录上限前耗尽请求额度。

## 来源隔离与旧库升级

- 来源绑定发生在任何元数据/关系缓存使用和请求之前；fixture 必定使用 Synthetic 标签。
  已绑定 DB 拒绝真实/fixture 双向混用，拒绝不同指纹的 fixture；错误不清库、不改标签。
- 同一文件复制到不同路径保持身份；内容或格式改变会改变指纹，需另用 DB。
  `--show-run` 从 DB 读取保存来源，不根据是否传 fixture 参数重新推断来源。
- 增量创建新表和 sample_run_id，不删除旧论文、边、别名、状态。
  旧库没有可验证的逐条记录，不能从计数或历史边补造 run；首次采集需重取关系一次。
  来源可信且缓存仍有效的种子元数据可以继续复用；之后沿用原 TTL 与续传机制。
- 旧库的所有论文/边都标注为 Semantic Scholar 时，可按这个已有来源声明绑定真实身份；
  混合/未知来源以及没有内容指纹的旧模拟库不自动认领，保留历史读取并要求使用新 DB。
  **此前错误标注的来源无法自动鉴定**；旧真实来源标签必须可信，不能据此替外部旧数据
  提供真实性保证。代码不会为无指纹旧模拟库猜测当前 fixture 就是其原始来源。

## 实际修改文件

| 路径 | 修改 |
|---|---|
| `src/papertrail/api.py` | RequestBudget、预算耗尽错误、实际重试计数、来源身份 |
| `src/papertrail/store.py` | 增量 schema、来源绑定、run 生命周期、记录复制、事务写入、get_sample |
| `src/papertrail/collector.py` | 参数验证、run 创建/结束、样本缓存复用、sample_totals、请求统计 |
| `src/papertrail/cli.py` | fixture 内容身份、请求预算参数、预校验、来源错误提示、--show-run |
| `tests/test_collection.py` | 仅两处手工写 fixture 的测试先显式绑定来源；原业务断言保持 |
| `tests/test_collection_runs.py`（新增） | 27 项参数化离线用例，全部使用临时 DB / mock |
| `README.md` | 样本入口、schema、预算、隔离、升级与运行示例 |
| `tasks/TASK-002A-COLLECTION-STORE.md` | 三项后续工作的状态链接到本任务；保留原历史验收证据 |
| 本文件（新增） | TASK-002A.1 需求、设计、证据和局限 |

未修改原实验脚本、TASK-001 测试、fixture 内容、依赖清单或 pytest.ini。

## 验收证据

使用现有 Python 3.11 虚拟环境，未安装依赖；HTTP 和等待在测试中 mock。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002a-pytest-cache
# 119 passed in 9.69s
.\.venv\Scripts\pytest.exe -q -o cache_dir=output/task002a-pytest-cache
# 119 passed in 9.67s
git diff --check
# 通过
```

TASK-001 的 31 项、TASK-002A 的 61 项和新增 27 项全部通过。
首轮为 90 passed / 2 failed：新隔离规则拒绝两处旧测试把手工 fixture 标成真实来源；
修复其初始化来源声明后通过，未删测试或放宽隔离规则。已有 safe.directory/LF-CRLF
提示未修改系统或 Git 配置。

| 验收目标 | 代表测试 / 证据 |
|---|---|
| 精确成员和原始统计 | `test_exact_membership_raw_counts_and_source`：4 节点、5 边、7 原始记录、1 未解析，方向和配额正确 |
| 缓存不改旧样本 | `test_cached_sample_survives_reopen_and_does_not_mutate_old_run`：重开 DB 零请求，新 run 复制记录且旧 run 内容不变 |
| 缩小预算 | `test_smaller_budget_excludes_historical_nodes_and_edges`：预算 1 / 0，旧多余节点和边不进入样本 |
| 节点相同但边不同 | `test_dropping_seed_does_not_induce_edges_from_history_between_sample_nodes`：减少种子后仍为 4 节点，但样本仅 3 边，全库 5 边 |
| 刷新和扩大预算 | refresh / expanded_budget 测试：新成员变化、旧 run 不变；续传复制重复和未解析前缀 |
| 失败与跳过 | `test_failure_sample_does_not_borrow_skipped_historical_edges`：失败 run 不借用跳过方向的历史边，恢复建立新 run |
| 事务 | `test_sql_failure_rolls_back_membership_and_marks_run_interrupted`：整页论文、边、成员和游标回滚，先前页保留 |
| 完整旧 schema 升级 | `test_full_legacy_schema_upgrade_refetches_membership_preserving_history`：元数据复用、关系重取、旧历史边保留但不纳入新样本；再运行缓存命中 |
| 来源隔离 | API/fixture 双向、不同 fixture 内容、旧混合/未知/无指纹模拟来源均在请求前拒绝 |
| 参数范围 | 上界、请求预算 0、400 位 TTL 整数等在客户端/DB 创建前拒绝 |
| 实际请求次数 | `test_request_budget_counts_actual_http_attempts_including_retries`：预算 1 / 4 均严格限制 session.get 次数，包含 503 重试 |
| 离线查询 | `test_show_run_uses_persisted_identity_without_network`：不创建 HTTP 客户端，读取正确成员及合成来源 |

还通过 subprocess 在 output 下的 TemporaryDirectory 执行 3 次离线采集，再独立进程
执行 --show-run；结束后清理临时测试目录，未创建正式用户 DB。

| run | 种子 / 被引预算 | 模拟请求次数 | 当前样本：论文 / 边 / 原始 / 未解析 | 历史：论文 / 边 |
|---|---|---:|---|---|
| 1 | 两篇 / 150 | 7 | 4 / 5 / 7 / 1 | 4 / 5 |
| 2 | 同上，命中缓存 | 0 | 4 / 5 / 7 / 1 | 4 / 5 |
| 3 | 第一篇 / 1 | 1 | 3 / 2 / 4 / 1 | 4 / 5 |

第三个 run 的 --show-run 在新进程中读出 3 篇、2 边，来源仍为合成数据。
完整可复现命令见 README，真实 API 命令仅为未来授权后的说明，未执行。

## 取舍、阅读与遗留风险

重要取舍：

1. 逐 run 复制原始记录前缀，避免复杂的共享页版本/快照指针；代价是完全缓存命中也增加
   SQLite 行数，当前没有自动历史清理或压缩。
2. 成员和关系观察冻结，论文属性仍为最新已知数据；避免引入元数据历史版本系统。
3. fixture 指纹与整库身份绑定，实现简单强隔离；代价是不能在一个 DB 中同时存真实和
   模拟空间，旧无指纹模拟 DB 不能直接续采。
4. 用实际请求预算控制异常/小页行为，不用预计页数提前拒绝常见短列表；大配置可能提前停止。

Owner 建议阅读三处：

- `store.py::get_sample`：为什么成员必须来自 run_items，不能从历史边表做诱导子图。
- `collector.py::collect_direction`：缓存复制、续传前缀和 run 游标如何配合。
- `api.py::request_json` / RequestBudget：为什么每次重试也必须计入全局请求预算。

手工检查：连续运行 README 的两篇离线演示，再只采第一篇并设置 citation-budget=1。
用各自 run_id 的 --show-run 比较，解释为什么历史仍是 4 篇/5 边，而新样本为 3 篇/2 边；
同时指出那条未解析参考记录为什么仍消耗原始记录预算。

尚未完成或需明确限定：

- [ ] 真实 API 分页、DOI 路径和长期限流行为验证，需 Owner 另行授权。
- [ ] Claude Code 独立只读审查与 Owner 验收。
- [ ] 其他不影响当前范围的 Minor，未宣称一并修复。
- offset 分页不是稳定快照，续传期间可能重排/遗漏；complete 仅代表 API 报告末页。
- API 若违反 limit，仍按已有策略保存实际返回记录、报告 incomplete 并停止，不能把该样本
  宣称为严格预算内成功；查看样本时应同时检查方向错误。
- 旧数据无法反向恢复真实历史采集批次；不伪造迁移前 run。旧错误来源标签无法自动鉴定。
- 不并发运行多个采集进程共享同一 DB；本轮没有实现跨进程预算、任务锁或严格墙钟终止。

实现后停止修改，等待审查；未开始 NetworkX 或 TASK-002B，未 Commit/Push。

## 最终限定修复（2026-11-10）

Owner 批准修复 --show-run 真正只读及必要的回归覆盖。本轮仍由 Codex 唯一实现，
没有其他编辑代理、依赖安装、真实 API/其他网络请求、NetworkX、Commit 或 Push。

### 根因、修复与实际文件

--show-run 原先调用 PaperStore 普通构造函数，普通构造会建表、迁移和更新别名确认标记。
文件存在检查不足以保证只读；即使只调用 get_sample，也已经可能修改了数据库。
此外 has_sample 仅校验行数和序号，不能发现同数量替换邻居 ID 或缺失标记。

本轮实际修改 6 个文件：

- `src/papertrail/store.py`：增加独立 `open_readonly`，read-only schema 验证和安全错误类型；
  增加缓存 sample_digest，检测成员内容变更。
- `src/papertrail/cli.py`：--show-run 改用只读路径，区分错误提示；参数错误与只读输出脱敏。
- `tests/test_collection_runs.py`：强化现有只读和旧库测试，补充缺失的异常 DB、篡改、参数组合等回归。
- `tests/test_collection.py`：扩展既有密钥测试，核对持久化输入/报告及 --show-run 输出。
- `README.md`：只读库接口、错误语义、缓存摘要与测试结果。
- 本任务记录：记录最终修复和验收证据。

本轮未改 api.py、collector.py、依赖或 TASK-002A 的原业务规则；这些路径中已有前轮
未提交改动仍保留。当前缓存失效后的重新采集直接复用原流程，不增加新的采样策略。

### 独立只读方式

`with PaperStore.open_readonly(path) as reader:` 绕过普通构造函数，用
`sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)` 打开。
as_uri 对路径空格/# 等正确编码；不拼接未转义的路径，不使用 immutable=1。

连接只执行 BEGIN、SELECT 和读取 table_info 的 PRAGMA；不建表、不迁移、不写数据。
一次读取事务覆盖 schema 检查与多个样本查询，遵守正常 SQLite/WAL 快照和锁规则。
关闭 reader 结束快照；未来分析可直接复用 reader.get_sample，不引入 NetworkX。

| 输入 | 结果 |
|---|---|
| 不存在的文件 / 非普通文件 | 明确提示不存在，不创建 DB，CLI 退出 1 |
| 0 字节文件 | 明确提示 empty (0 bytes)，不初始化，退出 1 |
| 不相关 SQLite 库 | 提示 Not a PaperTrail database，不添加表，退出 1 |
| 旧/不完整 PaperTrail schema | 提示只读路径不迁移，需由采集流程升级并产生可追溯 run，退出 1 |
| 非 SQLite 文件或无法读取 | 提示格式/访问权限/锁问题，不打印底层文件内容，退出 1 |
| 正常当前 schema | 可读取 run、来源和报告；不要求新缓存摘要列存在，不为查看而升级 |
| 无效 run ID / 损坏样本内容 | 给出检查 run ID/数据库完整性的提示，退出 1 |
| --show-run 与种子/fixture/refresh 同用 | 打开 DB 前报参数错误，退出 2 |

只读查询不自动修复 schema，不把 running 改写为 interrupted，也不更新缓存摘要。
WAL 的正常共享内存/锁机制保留；“不变”指该读取操作不改变数据库 schema/业务内容，
不能要求同时存在其他写入者时数据库文件始终不变。

### 缓存内容完整性与秘密输出

写入 checkpoint 时，同事务计算有序 run_items 的 SHA-256 摘要，覆盖 ordinal、
other_paper_id、missing_abstract 和 fetched_at。复用前同时检查成员所属种子、行数、
连续序号及摘要；删行或同数量修改也会失效，不会把被改动内容复制到新的缓存样本。
新 run 从第 0 页安全重采，原预算、TTL、完成/警告规则保持。

旧 checkpoint 无摘要时不自动为未知内容生成“可信”摘要，需要一次重新采集；
真正旧 TASK-002A schema 没有 sample_run_id 的非零断点同样不能恢复，必须从 0 重取。
摘要用于发现记录变化，不是签名；如果外部同时修改记录和摘要，不能据此保证真实性。
外部已改动的历史 run 不会自动恢复原文，需保留原备份或另行核查。

保存 run 时既有输入/报告脱敏仍生效。CLI 参数错误可能回显非法值，因此 parser.error
按当前配置的 API Key 脱敏；--show-run 对旧/外部提供的报告同样做显示脱敏，不写回 DB。
这两个显示路径可读取环境 Key 来隐藏它，但不会创建 HTTP 客户端或联网。

### 新增/强化测试和验证证据

对既有测试按需扩展，本轮用例总数从 119 到 134，未删除原断言。

| 场景 | 实际断言 |
|---|---|
| --show-run 正常读取 | 禁止调用普通构造函数；连接 URI 明确 mode=ro，未使用 immutable；查询前后文件字节和 iterdump 完全一致 |
| 连接真正只读 | SQL 跟踪仅 BEGIN/SELECT/PRAGMA；主动执行 UPDATE 被 SQLite 以 readonly 拒绝 |
| 异常 DB | missing、0 字节、不相关 notes 库、真实旧表结构、非 SQLite 文件分别返回提示，字节保持不变；notes 库仍只有 notes 表 |
| WAL 一致性 | 读取尚在 WAL 中的已提交 run；保持只读事务时看不到后来新提交的 run，关闭重开后可见 |
| 内容篡改 | 删除一条、保持行数但换邻居 ID、改摘要缺失标记：均不命中/续传，refs 重新从 offset=0 请求 |
| 真实旧 schema | 旧 complete 结果及 error + next_offset=1 均缺少归属/摘要字段和 run 表；升级后重取 offset=0，保留历史数据 |
| 冲突参数 | 种子、fixture、refresh 分别与 --show-run 同用，打开 DB 前退出 2 |
| 密钥输出 | 非法 run 参数/未知选项不回显 Key；保存的 inputs_json/report_json 无 Key；模拟外部旧报告含 Key 时只读显示仍脱敏且 DB 字节不变 |

全部测试使用临时 DB、合成 fixture/mock，无真实请求。完整命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002a-pytest-cache
# 134 passed in 11.98s
.\.venv\Scripts\pytest.exe -q -o cache_dir=output/task002a-pytest-cache
# 134 passed in 11.50s
```

首次完整运行亦为 134 passed in 12.34s；随后补强旧报告只读脱敏断言并以上述完整运行验证。
git diff --check 通过。已有 safe.directory / LF-CRLF 提示未改系统或 Git 配置。

Owner 建议阅读 `store.py::open_readonly`、`store.py::has_sample` 和只读入口对应测试。
手工检查可对离线 DB 执行 --show-run，确认正常读出 run；再对一个 0 字节临时文件执行，
应收到明确空文件提示，文件保持 0 字节。

剩余：真实 API/DOI 验证仍待授权；本轮未自动修改残留 running 状态或修复被外部改动的
历史 run。没有把只读打开变成完整 schema 审计或带签名的防篡改系统。
所有修改未暂存，等待 Owner 最终验收；不自动提交或开始 TASK-002B。
