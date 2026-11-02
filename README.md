# PaperTrail
A Python tool for exploring academic citation networks, analyzing research literature, and building explainable reading lists.

当前实现 **TASK-001 API 验证**及 **TASK-002A 一层引用采集与 SQLite 持久化**，
尚未实现图分析或 UI。范围见 [项目简报](docs/PROJECT_BRIEF.md)，证据见
[TASK-001](tasks/TASK-001-API-SPIKE.md) 和 [TASK-002A](tasks/TASK-002A-COLLECTION-STORE.md)。

## Windows / Python 环境

需要 Python **3.11+**。在仓库根目录用 PowerShell 执行以下命令；先确认
`python --version`，若 PATH 指向旧版本，使用已安装的 3.11+ 解释器完整路径。

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

直接使用虚拟环境解释器，无需激活脚本或修改 PowerShell 执行策略。
直接依赖仅为 requests、pytest；其余使用标准库。

## TASK-002A：离线演示与采集

以下示例从仓库根目录运行，使用现有 `.venv`，无需安装新依赖。
先运行离线演示：两篇合成种子，共得到 4 篇唯一论文、5 条唯一有向边。
这不是实际论文数据，使用独立的演示数据库，不与真实采集数据库混用。

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-demo.db --page-size 2
# 原样再运行一次：所有方向 cache_hit 为 true，network_citation_records 为 0。
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-demo.db --page-size 2
```

`--offline-fixture` 不创建 HTTP 客户端、不读取 API Key；fixture 缺失条目也不会回退到网络。
输出 `mode=offline_fixture`，数据库来源注明合成数据。输入支持 1–3 个带前缀外部 ID
或 S2 paperId；暂不提供标题检索。必须显式传入 `--db`，其父目录须已存在。

以下为**需另行授权后才执行的真实采集命令**，TASK-002A 未执行：

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:2002.08909 ARXIV:2005.11401 --db papertrail.db
# 强制重新查询元数据、从第 0 页刷新关系；已有论文和边仍保留。
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:2002.08909 ARXIV:2005.11401 --db papertrail.db --refresh
```

真实模式复用 TASK-001 的环境变量、限速、重试、timeout、SSL 验证和脱敏规则。
没有 Key 时仍可发起匿名请求，限流失败后停止本轮后续请求。CLI 退出码：
0 = 正常结束（含明确标注的预算截断）；1 = API/分页/本地读写失败；2 = CLI 参数错误。
JSON 总状态 `ok` 表示全部 API 报告末页，`bounded` 表示存在预算截断，
`incomplete` 表示存在失败或跳过；`complete` 不等于现实文献覆盖完整。

### 模块与 SQLite

| 路径 | 职责 |
|---|---|
| `src/papertrail/api.py` | 共用 HTTP 客户端、ID 检查、单页请求、分页协议校验 |
| `src/papertrail/store.py` | SQLite schema、upsert、别名、事务、查询缓存 |
| `src/papertrail/collector.py` | 规范化种子、一层扩展、配额、缓存与续传 |
| `src/papertrail/cli.py` / `scripts/collect.py` | 参数、报告、合成 fixture 适配器 / 直接运行入口 |

| 表 | 键与内容 |
|---|---|
| `papers` | `paper_id` 主键；title/year/authors_json/abstract/url/external_ids_json/reference_count/citation_count 可空；source、updated_at、独立的 metadata_fetched_at |
| `paper_aliases` | `alias` 主键，`paper_id` 外键；`confirmed` 记录直接确认，`conflicted` 标记附带 ID 冲突 |
| `citation_edges` | `(citing_paper_id, cited_paper_id)` 联合主键，双方外键；source、fetched_at |
| `fetch_state` | `(seed_id, direction)` 联合主键；status、next_offset、raw_count、unresolved_count、missing_abstract_count、complete/truncated、target_limit、error_kind/message、expected_count、warning_kind/message、started_at/updated_at |

时间使用 UTC ISO 8601。作者及外部 ID 用 JSON 文本保存，避免为本任务增加多张子表。
元数据 upsert 的 null、空字符串（含纯空白）、空列表和空字典不覆盖已知字段，数值 0
仍可正常更新；候选论文更新不会刷新种子的 `metadata_fetched_at`。
别名将 DOI 大小写和输入前缀统一，最终仍依靠 API 返回的 paperId 去重。
`externalIds` 附带别名仅作为线索：冲突时保留已有映射、设置 conflicted，并在报告的
`alias_conflicts` 中列出。未经直接确认或存在冲突的别名不能用于种子缓存/合并，必须
直接查询；查询失败不猜测其归属。直接查询成功后，仅该输入别名可按返回 paperId
确认或改映射，改映射会报告 `alias_reassigned_from`，不合并/删除两个论文记录。
canonical paperId 不允许映射到另一 paperId；`--refresh` 重新确认不同的外部输入别名。
无 paperId 的返回记录计入 unresolved，不造 ID、不建边。
`citationCount` 是 API 全局计数，不能解释成本地网络入度。

每一页的论文、引用边和分页状态在一个事务中提交。后续 API 失败保留已提交页；
SQL 写入失败整页回滚。元数据请求失败且没有 paperId 时只在本次报告记错，不能建立
有效的种子外键。数据库为成功观察的累计集合；刷新不会删除历史边，也不是快照替换。

### 预算、分页和缓存

- **References**：仅请求种子的直接参考文献，默认每篇安全上限 10,000 条原始记录
  （`--reference-cap`）。逐页处理直到 API 不再提供 `next`，或触及上限/失败。
- **Citations**：`--citation-budget` 默认全局 150 条**原始返回记录**。先规范化并去重
  种子，再均分配额，余数按输入顺序分配：两篇各 75，三篇各 50；预算 151 / 三篇为
  51、50、50。未用额度不重新分配。重复、缺 ID 记录也占配额；候选论文不会递归扩展。
- `--page-size` 默认 100，范围 1..1000；每次 limit 不超过该种子该方向的剩余额度。
  遵循 API 返回顺序，不做重要性排序。`next` 必须是连续、前进的整数偏移；缺失表示
  API 报告末页，null、回退、空页带 next、offset 不符等是分页错误。
  可靠末页累计返回数与元数据计数不等（少于或多于）时，仍为 complete，并持久保存
  `warning_kind=count_mismatch`、expected_count 和 raw_count；这是质量警告，可命中缓存，
  不导致 incomplete 或 CLI 退出码 1。有 next、请求失败或分页异常不能据此标为完成。
- 同次成功分页的 `raw_count`、`unresolved_count` 和 `missing_abstract_count` 包含
  缓存/恢复前缀；`network_raw_records` 只统计本次实际返回的关系记录；
  `network_citation_records` 汇总本次被引请求记录。`database_totals` 是整个数据库
  累计去重的论文/边数量，不能与本轮原始记录数混为一谈。重试失败没有返回记录。
- 默认 TTL **7 天**（`--cache-ttl-hours 168`）。关系缓存与断点续传统一从该轮
  `started_at` 计算，updated_at 仅表示最近状态更新；续传完成不会延长旧页寿命。
  种子元数据仍按 metadata_fetched_at 计算。有效的完整结果直接复用；相同配额的
  截断结果也可复用，但仍标记 `truncated=1, complete=0`。失败/部分结果不作为成功
  缓存；仅在这一轮采集的 started_at 仍在 TTL 内、next_offset 已知且配额不缩小时
  续传。缓存前缀占当前配额，重新执行不会自动再增加 150 条。
- 扩大配额可从有效断点继续；缩小配额时部分/截断状态从第 0 页重取，完整结果仅在
  raw_count 不超过新配额时可复用。TTL 到期或 `--refresh` 从第 0 页重取，
  保留历史论文和边。未知分页位置从第 0 页重试，依靠主键去重。
  `--cache-ttl-hours 0` 禁用缓存和跨运行续传。中断后的状态不能承诺不变快照：
  数据源可能在 offset 分页期间增删/重排记录；需要重新取样时使用 `--refresh`。
- 若 API 违反 limit 返回过多记录，保存实际已取得数据和数量，明确报错并停止，
  不把它伪装成严格预算内的成功结果。安全上限和配额不等于文献世界完整覆盖。

请求与分页约定沿用 TASK-001 中 2026-10-28 的文档核验；本轮仅离线验证，
没有重新联网核验协议、条款或额度，也没有进行真实分页测试。当前保留论文、图边和
缺失统计，不持久化逐条 contexts/intents 等证据字段；TASK-001 原始报告功能仍可用。

旧 TASK-002A 数据库打开时会增量补充别名确认/冲突和质量警告字段，保留论文、边及
进度。旧外部别名没有直接确认来源，首次作为输入时需重新确认；旧 count_mismatch
错误不自动改成成功，将重取一次后按新规则保存。暂缓事项见任务记录“后续工作”：
极端参数/请求预算保护、fixture/真实 DB 严格隔离尚未实现，须在真实采集前解决；
样本归属 D1 须在 TASK-002B 前解决。以上限制不因本轮修复而视为完成。

## TASK-001：API Key 与实验脚本

程序只读取环境变量 `SEMANTIC_SCHOLAR_API_KEY`，通过 `x-api-key` 请求头传递。
请在本机安全配置该变量；不要把密钥写进源代码、命令历史、聊天或提交。
程序不加载 `.env`，不输出认证头或底层异常文本。没有密钥也可尝试公开端点，
但匿名请求共享限流资源，遇到第二次 429 会停止本轮任务。

```powershell
# 第一篇正常后，再运行第二篇；默认各取前 5 条关系。
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:1706.03762
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:2303.04137

# 两个上限独立设置，范围均为 1..1000。
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:1706.03762 --references-limit 10 --citations-limit 5

# 单独参考文献覆盖检查：一个端点、最多一页；不会请求元数据或被引记录。
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:1706.03762 --references-only --references-limit 1000
```

普通模式先读取元数据，再用返回的稳定 `paperId` 请求 references 和 citations。
单独参考文献模式使用输入 ID 作为边的种子端；需要稳定 S2 ID 时，传入之前
元数据响应中的 `paperId`。支持 S2 ID 或带前缀的 DOI、ARXIV、CorpusId、ACL、
MAG、PMID、PMCID、URL；程序只做基本格式检查，是否存在由 API 确认。

三个端点都显式指定 `fields`。元数据包含 paperId、externalIds、title、year、authors、
url、abstract、referenceCount、citationCount；关系端点额外请求 contexts、intents、
isInfluential。这些端点的论文字段写作 `title,abstract,...`，不加 `citedPaper.` 前缀。

每次运行在 Git 忽略的 `data/raw/<UTC时间戳>.json` 保存一个脱敏报告，内含各端点
的原响应、请求参数、获取时间、尝试记录及质量统计。成功部分会保留；未执行的
端点标为 skipped。终端只显示各端点质量摘要、状态及整次运行最多 3 条示例边，
示例包含两端论文的 ID、title、year；方向为 citing -> cited。
缺少标题或年份显示 null；单独参考文献模式不额外请求种子元数据。
完整响应及逐条关系证据仍保存在 JSON，原有字段不变，仅增加条目的 title/year。
退出码：0 表示所有计划请求成功且结构可解析，1 表示 API/解析/写盘失败，
2 表示 CLI 参数错误。空列表是成功响应，但不保证适合演示。

## 请求边界与错误处理

- 全部请求（含匿名与重试）起始时间至少间隔 1.1 秒。串行运行，不并发启动多个实例。
- 连接/读取超时为 5/20 秒；每个端点最多 3 次尝试。普通模式最多 9 次，
  单独参考文献模式最多 3 次，不自动翻页。
- 429、5xx、连接失败、超时及明确的暂时性 SSL 中断（EOF、连接重置/中止）
  有限重试；共用每端点最多 3 次尝试的上限，退避为 2、4 秒。
  支持 `Retry-After` 秒数或 HTTP 日期；要求等待超过 10 秒时结束，不提前重试。
- 无密钥时第二次 429 即停止；401/403、确定的证书验证失败、未知 SSL 错误
  及耗尽重试的网络/服务错误停止后续请求。SSL 校验始终开启，不通过 `verify=False` 绕过。
- 400/404 不重试；元数据失败时不运行依赖请求。单个关系响应解析失败时仍可
  检查另一关系端点。非 JSON、`data: null` 或缺少 `data` 不会冒充空列表。

超时是连接/读取超时，不是整个命令的严格墙钟时限。请求预算不跨进程共享。

## 数据质量与解释

- 边方向固定为 `citing -> cited`：references 是“种子 → 参考论文”，
  citations 是“被引来源论文 → 种子”。
- `returned/resolved/unresolved` 按返回条目计数；`paperId: null`、缺少 ID
  或缺少论文对象的条目保留并归为 unresolved，不伪造 ID，不构造该条边。
- 唯一论文和边按稳定 ID 去重统计，原响应保留重复项。
- `missing_paper_fields.abstract` 是本页摘要缺失数（含空字符串、null 或缺字段）；
  unresolved 条目也计入分母。元数据单独记录各字段状态。
- contexts、intents、isInfluential 区分 available / empty / null / missing。
  `isInfluential: false` 是有效值，不是缺失，也不表示论文质量低。
- `truncated: true` 表示有合法下一页但主动不取；缺少 `next` 按官方协议记录为
  `end_reported_by_api`、`truncated: false`。异常 `next` 或末页与元数据计数
  不一致时为 `truncated: null`（未知）。单独参考文献模式没有元数据计数可比较。
- “API 报告末页”不等于完整参考文献覆盖。空列表可能涉及收录、出版商省略或
  数据缺失，不能据此推断论文没有引用。首屏顺序不是重要性排名。
- 上下文、意图和影响性来自 Semantic Scholar，可能缺失或不准确；本任务不据此推荐论文。

## 离线测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\pytest.exe -q
```

两种入口均从仓库根目录运行，`pytest.ini` 的 `pythonpath = . src` 用于发现项目模块。
8 组核心场景通过参数化覆盖：成功解析/字段/方向/去重/限速、空值与分页、
CLI/无密钥/单端点/保存脱敏、非重试 HTTP 错误、429/5xx/Retry-After、
SSL/超时/连接错误、坏响应/部分成功、写盘失败。网络及等待均被模拟，
测试不会读取真实密钥或发出 API 请求。
`tests/fixtures/s2_spike.json` 是明确标注的**合成数据**，不是线上响应。

TASK-002A 新增 `tests/test_collection.py` 及 `tests/fixtures/s2_collection.json`，
覆盖分页/预算、SQLite 事务和重开、跨种子和外部 ID 去重、缓存/刷新、限流与 SSL
失败恢复及独立审查回归。目前 **92 项离线测试通过**，其中 TASK-001 原有 31 项测试未修改。
所有数据库测试使用 pytest 临时目录。如果本机旧 `.pytest_cache` 不可写，可指定新的
忽略目录：`python -m pytest -q -o cache_dir=output/task002a-pytest-cache`。

## 官方资料与来源

2026-10-28 核验：[Graph API 文档](https://api.semanticscholar.org/api-docs/graphs)、
[官方 OpenAPI 规范](https://api.semanticscholar.org/graph/v1/swagger.json)、
[官方 API 介绍](https://webflow.semanticscholar.org/product/api)、
[API 使用条款](https://webflow.semanticscholar.org/product/api/license)。
主站部分页面读取返回 403，上述 webflow 子域为同一服务的官方页面。
数据来源标注为 **Semantic Scholar**；数据及第三方内容的使用仍受各自许可约束。
本仓库不提交真实原始响应，不提供数据再分发或商业使用许可判断。
