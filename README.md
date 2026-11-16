# PaperTrail
A Python tool for exploring academic citation networks, analyzing research literature, and building explainable reading lists.

当前实现 **TASK-001 API 验证**、**TASK-002A 一层引用采集与 SQLite 持久化**及
**TASK-002A.1 采集安全与样本归属**、**TASK-002B 引用图基础分析**及
**TASK-003 候选发现、筛选、排序与样本统计**，以及
**TASK-004 Streamlit MVP 与 CSV/Markdown 阅读清单导出**。范围见 [项目简报](docs/PROJECT_BRIEF.md)，证据见
[TASK-001](tasks/TASK-001-API-SPIKE.md)、[TASK-002A](tasks/TASK-002A-COLLECTION-STORE.md)
和 [TASK-002A.1](tasks/TASK-002A.1-SAFETY-SAMPLES.md)、[TASK-002B](tasks/TASK-002B-GRAPH.md)、
[TASK-003](tasks/TASK-003-DISCOVERY.md)、[TASK-004](tasks/TASK-004-UI.md)。

## Windows / Python 环境

需要 Python **3.11+**。在仓库根目录用 PowerShell 执行以下命令；先确认
`python --version`，若 PATH 指向旧版本，使用已安装的 3.11+ 解释器完整路径。

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

直接使用虚拟环境解释器，无需激活脚本或修改 PowerShell 执行策略。
直接依赖为 requests、pytest、NetworkX、Streamlit、Plotly。
TASK-002B 在现有 Python 3.11.7 / NetworkX 3.6.1 环境验证。

## TASK-004：启动图形界面

在仓库根目录运行（已有环境只需安装更新后的 requirements）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

浏览器打开 [本机 PaperTrail](http://127.0.0.1:8501)。无需激活虚拟环境。
`.streamlit/config.toml` 默认只监听本机并关闭 Streamlit 使用统计上传；不包含密钥。
TASK-004 验证环境为 Python 3.11.7、Streamlit 1.67.0、Plotly 6.9.0、NetworkX 3.6.1。
更新代码后若页面仍显示旧版，请在运行服务的终端按 Ctrl+C，再执行上述启动命令；
仅刷新网页不保证已导入的模块重新加载。终端中的服务必须保持运行。

### 完整离线演示

依赖首次安装完成后，以下流程无需网络、无需 API Key：

1. 点击侧栏 **打开演示（无需网络）**。系统使用现有 `tests/fixtures/s2_collection.json`，
   在 `output/ui-demo/` 创建按 fixture 指纹命名的独立数据库；再次打开复用已完成 run。
   页面应显示 4 篇论文、5 条引用边、2 篇种子、2 篇非种子候选、2 篇缺年份。
2. **引用网络**：橙色菱形为种子，蓝色圆点为非种子；箭头 A→B 表示 A 引用 B。
   点击蓝色节点或使用“选择论文节点查看详情”，核对标题、年份、摘要、样本入出度。
   Shared synthetic reference 的摘要为 `Example abstract.`；Synthetic citing paper 引用 2/2 颗种子。
   显示上限可调为 3–200，优先种子、其余按 ID；只影响画图，不缩小分析样本。
3. **候选论文**：分别查看“被种子引用”与“引用种子”；各自默认按对应种子数量降序。
   关系涉及至少 2 颗种子才标“共同”，并显示参与比例。同值篇数含自身，ID 次序不代表价值。
   年份/全局引用量范围、逗号分隔关键词可组合筛选，也可改用年份或全局引用量排序。
   演示候选年份与全局引用量均未知，启用相应数值筛选后为空是预期结果；关闭即可恢复。
4. **样本统计**：查看 2024/2025 各 1 篇及缺年份 2 篇、缺摘要 3 篇。
   输入 `synthetic, example`，分别得到标题/摘要/标题或摘要论文数 **4/0/4** 和 **0/1/1**。
   统计复用完整当前 run，含种子，忽略大小写、按字面子串匹配，每篇计一次；并集不是两列相加。
   图表仅描述当前采集样本，不能推断整个领域趋势。原始关系记录与唯一论文的缺失数口径不同。
5. **阅读清单**：从节点详情加入，或用多选框手动选择种子/候选；填写阅读理由，下载 CSV 和 Markdown。
   两种格式都有标题、年份、来源链接、类别、关系依据、阅读理由、来源标记、采集范围、run_id 和 paperId。
   CSV 使用 UTF-8 BOM，并转义可能被表格程序当成公式的文本；Markdown 将外部文本作为字面内容，
   保留中文、引号与换行，图片/链接语法不会执行。仅来源栏生成通过格式与 HTTP(S) 协议检查的链接。
   合成论文始终标为演示数据，**来源链接留空，不把虚构 DOI/arXiv 变成真实论文链接**。

浏览器会话中按“数据库绝对路径 + run_id”保存打开的分析结果、选择和阅读理由。
筛选、切换标签页、点击节点、下载不会重新采集；切换样本后各自清单仍保留。
关闭／刷新浏览器或重启服务可能结束会话，清单尚未写回数据库，请及时下载。
重新打开同一 run 会刷新最新已知元数据；此版本不提供历史元数据快照。

### 打开已有样本与新建采集

“已有样本”中输入数据库路径，点击“读取任务列表”并选择任务，或直接输入 run_id 打开。
打开过程只读，不迁移数据库、不查询 API；失败、未完成或不一致的样本显示错误且不保留旧图冒充新结果。
正常 `bounded` 样本可以打开，顶部保留中文预算截断警告、实际截断方向及数量。

真实采集须先获得 Owner 授权，本轮**未执行真实 API 查询或采集**：

1. 选择“新建采集”，每行输入一篇 DOI、arXiv ID 或标题，共 1–3 篇。
   支持裸 DOI、`DOI:`、DOI URL、裸 arXiv、`ARXIV:`、arXiv `/abs/` URL，也接受 S2 paperId。
2. 授权后勾选相应复选框，点击“查询种子候选（联网）”。标题通过 relevance search 返回最多 5 条，
   显示标题、年份、作者和来源链接；不会自动采用第一条。没有结果时须修改输入重新查询。
   仅标题请求会将连字符替换为空格，例如 `Pre-training` → `Pre training`；界面原输入和 DOI/arXiv 不变。
3. 逐项选择，再点击“确认这些种子”。修改输入或候选选择会使原确认失效。
4. 选择与演示库分开的真实数据库，设置全局被引预算（默认 150，UI 范围 0–200）和
   请求尝试预算（默认 100，UI 范围 1–100），点击“开始采集（联网）”。只扩展一层。

同一会话中，每组参数的一次操作只提交一次；运行中、已完成、失败或中断后均不因重绘自动重发。
要再次操作，先点“准备重新查询／重试”或“准备重新采集／重试”，再点对应提交按钮；准备步骤不联网。
查询重试后须重新人工确认种子。输入、确认结果、数据库路径及预算决定对应操作状态，改回旧参数
仍保留原状态。重新采集沿用 collector 缓存规则，不等于强制刷新。这是会话内保护，不是跨浏览器或跨进程锁。

查询阶段独立上限 9 次 HTTP 尝试，含重试；采集阶段按现有 collector 的预算计数。
未使用份额不重新分配；参考文献每种子最多 10000 条、每页最多 100、缓存 TTL 7 天。
本轮确认后的稳定 paperId 交给 collector 再取/复用元数据，查询结果不直接写入采集库。
界面提供运行状态、run_id、实际请求尝试数和失败详情；中断/失败数据不能冒充有效分析样本。
无 Key 可尝试匿名请求，但实际可用性和限流需要另行授权验收。

### 验证范围

TASK-004 初版为 319 项；TASK-004.1 新增 36 项后完整回归 **355 passed in 85.76s**，
其中 UI 定向回归 **77 passed in 24.62s**。覆盖批量箭头、图缓存隔离、全样本关键词统计、
重复提交/重试状态、连字符查询、异常元数据与 Markdown 安全。
离线测试使用临时库、合成 fixture、mock HTTP 和 Streamlit AppTest；不访问真实论文 API。
AppTest 覆盖控件与重绘行为；实际浏览器另已验证演示入口、节点点击到详情、加入清单、
中文阅读理由、CSV/Markdown 下载，以及年份筛选和样本统计。
TASK-004.1 另实测了关键词表与特殊文本 Markdown 渲染；没有执行真实 API。
图布局与 Plotly 图对象在当前打开样本内按身份和节点上限缓存；重新打开会刷新，无关控件不重算布局。
每条边先生成箭头数据，再一次提交布局。100/200 节点（293/593 边）各三次实测，构图中位耗时
分别由 **27.547/119.665 秒降为 0.088/0.277 秒**；测量含布局与图对象创建，不代表浏览器帧率。
真实搜索结果、真实来源链接目标、真实采集/限流、浏览器刷新后的人工恢复及大图使用体验
仍需按 [TASK-004 人工验收清单](tasks/TASK-004-UI.md) 检查，不能视为已通过真实端到端测试。
快速连续点击的浏览器事件排队仍需人工验收；自动化已检查重复/旧尝试拒绝、失败保留及主动重试。
可选 M7（图内高亮与下拉选择同步）暂缓；详情由当前选择控制，不承诺同步清除 Plotly 原高亮。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task0041-pytest-cache
# 离线性能测量；不是 CI 时间阈值测试，输出到 Git 忽略目录
.\.venv\Scripts\python.exe scripts/benchmark_ui_graph.py --label local --repeats 3 --output output/task0041-performance/local.json
```

## TASK-003：候选发现、筛选、排序与样本统计

使用现有 `.venv`，无需新增依赖。以下只用合成 fixture；采集命令创建/填充独立演示库，
`discover.py` 分析命令完全只读、不联网。已有演示库可直接分析实际 `run_id`。

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-discovery-demo.db --page-size 2
# 用采集输出的实际 run_id 替换 1。
.\.venv\Scripts\python.exe scripts/discover.py --db papertrail-discovery-demo.db --run-id 1 --top 3
.\.venv\Scripts\python.exe scripts/discover.py --db papertrail-discovery-demo.db --run-id 1 --category shared_citing --sort-by cites_seed_count --keyword synthetic --json
# 组合范围示例：现有 fixture 的非种子年份/全局引用量均缺失，此查询应返回空候选。
.\.venv\Scripts\python.exe scripts/discover.py --db papertrail-discovery-demo.db --run-id 1 --year-min 2020 --year-max 2025 --citation-min 0 --citation-max 100
```

默认只显示非种子候选；`--include-seeds` 把符合筛选的种子放入独立 `seeds` 列表。
种子没有候选类别，启用 `--category` 后即使设置 `--include-seeds` 也会被类别筛选排除。
旧 `scripts/analyze.py` 保留 TASK-002B 的图诊断行为（包含种子的入度表），候选阅读清单应使用
新的 `scripts/discover.py` 或下面的 `discover()` 接口。

### 候选依据与排序

| 类别 / 字段 | 当前 run 中的含义 |
|---|---|
| `shared_reference`（被种子引用） | 至少一篇种子存在 `种子 → 该论文` |
| `referenced_by_seed_count` / `referenced_by_seed_ids` | 引用该论文的不同种子数 / 排序后的种子 ID 列表 |
| `shared_citing`（引用种子） | 至少一篇种子存在 `该论文 → 种子`；不推断发表时间一定更晚 |
| `cites_seed_count` / `cites_seed_ids` | 该论文引用的不同种子数 / 排序后的种子 ID 列表 |
| `local_in_degree` / `local_out_degree` | 所有已观察入边 / 出边数，不能用它们替代与种子的关系计数 |
| `evidence` / `reason` | 类别、种子 ID、数量和边方向的结构化依据 / 可读解释 |

内部类别从一篇种子起计；UI 只在关系涉及至少两颗种子时使用“共同”。
同一非种子论文可同时属于两类；重复观察的同一条边只计一次。
每行保留标题、摘要、年份、来源、URL、externalIds、获取时间及全局引用量，缺失值保留 null。

`--sort-by` 可取 `referenced_by_seed_count`（默认）、`cites_seed_count`、
`global_citation_count`、`year`，均降序。只使用一个主指标，同值按 paperId 升序稳定展示，
**paperId 次序不表示研究价值**。`rank` 使用并列名次 1、1、3；`tie_count` 是截取 top 前、
该筛选组中同值论文的总数，包含自身。缺失值排在末尾，`rank=null, tie_count=0`，不是零分或并列测量。
`--top` 默认 5，范围 1..100，分别限制候选列表与种子列表；JSON 同样遵循该限制。
筛选后的总数另存于 `selection.candidate_count_before_top/seed_count_before_top`。

### 筛选与统计口径

- 年份与全局引用量范围均包含端点，支持只设上下限之一。启用某数值筛选时排除该字段为 null
  的论文；未启用时保留它们。**引用量 0 是已知值，null 是未知值**。
- `--keyword` 搜索标题或摘要的字面子串，采用 Unicode `casefold()` 忽略大小写，去除查询两端空白；
  不分词、不解释正则表达式。可重复传入，多关键词须全部命中，每个词可命中不同字段；空关键词报错。
- 不同筛选条件之间是 AND；重复 `--category` 是 OR。所有筛选只改变显示列表，不重建图、不改变度数。
- `statistics` 始终统计筛选前的**当前采集样本**：唯一论文总数、种子/非种子数、两类候选数、
  双重类别数、年份分布及年份/标题/摘要/全局引用量/链接/作者/外部 ID 的缺失数。
  两类候选数可能重叠，不能直接相加作为候选总数。
- 关键词统计复用查询词，逐词统计标题、摘要、任一字段命中的**论文数**；同一篇重复出现一个词仍只计一次，
  同时命中标题和摘要也只在任一字段总数中计一次。未传关键词时返回空列表。
  年份缺失不放进年份分布，单独计数；元数据缺失统计按唯一论文计算，与采集报告按原始记录计算的缺失数不同。

### Python / 后续 Streamlit 接口

在已能导入 `papertrail` 的 Python 环境中调用；仓库脚本已处理 `src` 路径，pytest 也已配置：

```python
from papertrail.discovery import discover

result = discover(
    "papertrail-discovery-demo.db", run_id=1,
    categories=["shared_citing"], keywords=["synthetic"],
    sort_by="cites_seed_count", include_seeds=False, top=None,
)
# result["candidates"] -> 候选表；result["seeds"] -> 独立种子表
# result["statistics"] -> 当前样本统计，不随筛选缩小
# result["sample"]["warnings"/"limitations"] -> 界面必须同时展示的说明
```

返回 `schema_version=1`，顶层字段固定为 `sample/statistics/selection/ranking/candidates/seeds`，
可直接 JSON 序列化。Python `top=None`（默认）返回所有匹配行；CLI 默认为 5 行。
`year_min/year_max/citation_min/citation_max` 默认 None，`keywords/categories` 为字符串列表或元组。
如已有 `load_graph()` 返回的未修改图，可调用 `summarize_discovery(graph, **options)` 复用；
`discover_papers/filter_papers/rank_papers/sample_statistics` 分别负责发现、筛选、排序和统计。
`rank_papers` 拒绝把种子与非种子混排。后续界面不要移除图节点后再算原样本统计。

只读入口复用 `load_graph` 的全部状态与来源校验：支持 `complete` 和合法 `budget_truncated`，
拒绝失败、不一致或未完成样本；不读取历史 `citation_edges` 补边，不写 SQLite。
结果保留 run 来源、采集预算、截断方向、覆盖信息与警告。元数据是读取时的**最新已知值**，
不是 run 的历史属性快照；单层采集可能缺少非种子之间的边，局部出度也不等于完整参考文献数。
所有数量、年份与关键词统计仅描述当前样本，不能宣称整个领域的发展趋势。

退出码沿用分析入口：0 成功，1 数据库/run/样本错误，2 参数错误；错误不输出部分 JSON。
文本输出保留终端编码并转义无法表示的字符；JSON 使用 ASCII 安全转义。CLI 仅为脱敏读取环境中的 Key，
不创建 HTTP 客户端。纯 Python 返回数据保留原始元数据，外部展示者应自行遵守输出脱敏规则。

**已验证的合成示例：** 两篇种子 A/B，共 4 篇论文。C（Shared synthetic reference）被 A/B 引用，
`referenced_by_seed_count=2, local_in_degree=2`；D（Synthetic citing paper）引用 A/B，
`cites_seed_count=2, local_out_degree=2`。默认候选只有 C/D，种子不进入榜单。
年份 2024/2025 各 1 篇、缺年份 2 篇、缺摘要 3 篇；`synthetic` 在标题中命中 4 篇。
这些都不是实际论文或真实引用量。单种子、被引预算 1 的示例可正常分析并保留 `budget_truncated` 警告。

## TASK-002B：只读引用图分析

先按下一节的两篇种子命令生成离线数据库，再使用采集输出的实际 `run_id`：

```powershell
# 摘要、覆盖提示及按样本入度排序的前三个候选；不会联网。
.\.venv\Scripts\python.exe scripts/analyze.py --db papertrail-demo.db --run-id 1 --top 3
# 完整结构化 JSON，包含所有节点属性、边、指标、候选依据及局限。
.\.venv\Scripts\python.exe scripts/analyze.py --db papertrail-demo.db --run-id 1 --json
```

`--db` 和 `--run-id` 必填；`--top` 默认 5，范围 1..100，只控制候选数量。
退出码：0 = 成功；1 = 数据库/run 不存在、不适合分析或内容不一致；2 = 参数错误。
失败原因写入 stderr，不输出部分分析 JSON。整个入口复用
`PaperStore.open_readonly()`；不创建数据库、不迁移、不改采集状态、不更新缓存。
CLI 可读取环境中的 API Key **仅用于输出脱敏**，不会创建 HTTP 客户端。

**输出编码：** 文本保留 Python 选择的终端/重定向编码，stdout 和 stderr 均设置
`errors="backslashreplace"`。可表示的中文/Unicode 原样输出；cp936 等编码无法表示的字符
显示为 `\u0151`、`\U0001f9ea` 等转义，不因标题编码失败而崩溃。不强制终端改用 UTF-8，
也不要求设置 PYTHONIOENCODING。`--json` 使用 ASCII 安全的 Unicode 转义，JSON 解码后可
恢复完整原文；需程序读取或无损交换时优先使用 JSON。转义保护不替代终端字体支持。

**分析前检查（TASK-002B.1）：** run 与报告的 status 必须一致，且为 `ok` 或 `bounded`，
有结束时间和报告；核对来源、种子、原始计数、样本总数和预算。
`ok` 的所有方向必须完整结束，不能含截断方向；`bounded` 至少有一个有效预算截断方向，
其余方向完整结束。截断要求 `raw_count == target_limit == 本次 limit`，其中 references
使用 reference_cap，citations 使用该种子的 citation_quota；complete=0、truncated=1，
无错误和 stop_run，允许仍有 next_offset。**预算截断是达到记录配额后主动停止，不是采集失败。**
`incomplete`、`interrupted`、`running`、`error`、未知状态及 partial/skipped/error 方向仍拒绝。
请求预算耗尽造成的失败不能用顶层 `ok` 或 `bounded` 掩盖；恰好用完请求次数但各方向有效结束则允许。
完整缓存可以保留旧 target_limit，分析核对实际记录同时符合原上限和本次配额。

JSON 和文本摘要均标明采集范围及截断方向。`collection_scope=complete` 只表示所有方向到达
数据源报告末页；`budget_truncated` 表示存在按配额停止的方向，**两者都不是完整文献网络**。
每个截断方向给出 seed_id、direction、raw_count、target_limit、expected_count，并警告
结果遵循 API 顺序，不是“最重要的 N 篇”。以下可用现有 fixture 离线体验预算截断：

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-bounded-demo.db --citation-budget 1 --page-size 2
# 将 1 替换为上一步实际输出的 run_id。
.\.venv\Scripts\python.exe scripts/analyze.py --db papertrail-bounded-demo.db --run-id 1 --top 3
```

已到末页但含缺 ID 条目、缺摘要或 count_mismatch 的样本可以分析，必须显示相应警告。
未解析条目不生成占位节点或边；无已解析论文的空样本报错，有种子但无边则正常保留节点。
检查的是已保存证据的一致性，不提供数据真实性认证或带签名的防篡改保证。

**图与指标：** `load_graph(db_path, run_id)` 返回 NetworkX `DiGraph`。
节点和边只来自 `get_sample(run_id)`，不读取 `citation_edges` 历史累计边。
`A → B` 表示 A 引用 B，同一有序端点只保留一条边。先加全部节点，再加边，保留孤立种子。
自环 `A → A` 保留，计作一条唯一边，并各增加一次 A 的入度和出度，不改变弱连通分量数；
`self_loop_count` 单列报告，不能据此把自引解释为独立论文的认可。

| JSON 字段 | 含义 / 计算方法 |
|---|---|
| `statistics.paper_count` / `unique_edge_count` | 图节点数 / 去重有向边数，含自环 |
| `nodes[].local_in_degree` | 指向该节点的样本边数，即样本内观察到的被引数 |
| `nodes[].local_out_degree` | 该节点指向其他节点或自身的样本边数 |
| `nodes[].global_citation_count` | SQLite 中保存的 Semantic Scholar `citationCount`；可空，fixture 中为合成值 |
| `statistics.weak_component_count` | 忽略方向后相互可达的最大节点组数量，孤立节点各算一组 |
| `statistics.isolated_node_count` / `self_loop_count` | 无任何关联边的节点数 / 自环边数 |
| `sample.coverage` | 原始/已解析/未解析记录数、采集时缺摘要记录数、重复边观察数 |
| `sample.collection_scope` | `complete` / `budget_truncated`，采集范围，不代表完整文献网络 |
| `sample.truncated_directions` | 每个截断方向的种子、方向、实际数量、上限和来源预期数量；无截断时为空列表 |
| `sample.direction_states` | 所有方向的状态、raw_count、target_limit、expected_count（缺失时 null），不限于 count_mismatch |
| `sample.warnings` / `limitations` | 数据问题及解释限制，后续界面必须一同展示 |

节点属性含 `paperId/title/year/url/authors/externalIds/abstract`、全局引用量、来源、时间和
`is_seed`；缺失字段保留 null。**这些是同一次只读事务中读取的最新已知元数据，不是该 run
采集时的历史属性快照**。样本成员固定，重新分析旧 run 的标题/全局引用数仍可能变化。

`summarize_graph(graph, top=5)` 返回可 JSON 序列化的
`sample/statistics/nodes/edges/candidates/candidate_order`，供后续界面复用。
应直接传入 `load_graph` 返回的未修改图，以保持样本证据与指标一致。
候选仅按正的 `local_in_degree` 降序排列，同值按 paperId 升序，包含种子；不使用全局引用量
打破平局，也不生成复杂评分。局部入度和全局引用量都不等于质量、经典程度或 baseline 地位。
候选字典也保留 `is_seed`，种子 reason 提示入度反映受配额约束的采样 citing 论文，文本加
`[seed]` 标记。种子入度受被引采样配额影响，不能与非种子入度直接比较；候选表只是样本观察顺序。

现有合成 fixture 的实际结果：4 篇论文、5 条边、1 个弱连通分量、0 个孤立节点/自环；
7 条原始记录中 1 条未解析、1 次重复边观察、6 条缺摘要（原始记录口径，包含重复项）。
候选 A、C、B 的局部入度分别为 2、2、1；全局引用字段分别为 2、null、1。
单层采集通常漏掉候选之间的相互引用，以上结果仅描述当前合成样本，不能外推到领域。

## TASK-002A：离线演示与采集

以下示例从仓库根目录运行，使用已安装上述依赖的 `.venv`。
先运行离线演示：两篇合成种子，共得到 4 篇唯一论文、5 条唯一有向边。
这不是实际论文数据，使用独立的演示数据库，不与真实采集数据库混用。

```powershell
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-demo.db --page-size 2
# 按输出的 run_id 查看样本；下例的 1 请替换成实际 run_id。不会联网。
.\.venv\Scripts\python.exe scripts/collect.py --db papertrail-demo.db --show-run 1
# 原样再运行一次：所有方向 cache_hit 为 true，network_citation_records 为 0。
.\.venv\Scripts\python.exe scripts/collect.py ARXIV:0000.00001 ARXIV:0000.00002 --offline-fixture tests/fixtures/s2_collection.json --db papertrail-demo.db --page-size 2
```

`--offline-fixture` 不创建 HTTP 客户端、不读取 API Key；fixture 缺失条目也不会回退到网络。
输出 `mode=offline_fixture`，数据库来源注明合成数据。输入支持 1–3 个带前缀外部 ID
或 S2 paperId；此 CLI 不提供标题检索（图形界面已支持）。必须显式传入 `--db`，其父目录须已存在。
每次采集（包括完全命中缓存）都有独立 run_id；`--show-run` 读取该次样本成员、配置、
来源、原始计数和完成报告，不能与 seeds/fixture/refresh 一起使用。
此入口使用独立的 `PaperStore.open_readonly(path)`，通过 SQLite URI `mode=ro` 打开，
不运行普通构造函数，不建表、不迁移、不更新状态，也不创建不存在的 DB。
缺失文件、0 字节文件、不相关 SQLite 库、旧/不完整 schema 分别提示原因并退出 1；
参数组合错误退出 2。旧库需由采集流程显式升级，只读查看不会替它升级。

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
| `src/papertrail/api.py` | 共用 HTTP 客户端、ID 检查、单页请求、分页协议校验、跨请求重试预算 |
| `src/papertrail/store.py` | SQLite、别名、事务、缓存记录归属、样本查询与来源绑定 |
| `src/papertrail/collector.py` | 规范化种子、一层扩展、配额、缓存与续传、run 生命周期 |
| `src/papertrail/cli.py` / `scripts/collect.py` | 参数、报告、合成 fixture 适配器 / 直接运行入口 |
| `src/papertrail/analysis.py` | 只读 run 校验、DiGraph、样本指标和候选依据 |
| `src/papertrail/analysis_cli.py` / `scripts/analyze.py` | 分析摘要 / JSON 命令行入口 |

| 表 | 键与内容 |
|---|---|
| `papers` | `paper_id` 主键；title/year/authors_json/abstract/url/external_ids_json/reference_count/citation_count 可空；source、updated_at、独立的 metadata_fetched_at |
| `paper_aliases` | `alias` 主键，`paper_id` 外键；`confirmed` 记录直接确认，`conflicted` 标记附带 ID 冲突 |
| `citation_edges` | `(citing_paper_id, cited_paper_id)` 联合主键，双方外键；source、fetched_at |
| `fetch_state` | `(seed_id, direction)` 联合主键；原计数/状态/时间字段保留，sample_run_id 指向可复用记录所在 run，sample_digest 校验其内容 |
| `dataset_identity` | 单行数据库身份：source_key、source；真实 API 与 fixture 指纹严格区分 |
| `collection_runs` | run_id 主键；输入、预算/配置、来源、开始/结束时间、状态、脱敏报告 |
| `run_seeds` | `(run_id, seed_id)` 主键；规范化种子及其被引配额 |
| `run_items` | `(run_id, seed_id, direction, ordinal)` 主键；每条原始关系记录的邻居 ID（可空）、摘要缺失标记、实际获取时间 |

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

### 当前样本与历史数据

后续分析可复用独立只读入口（需在项目 Python 导入环境中运行）：

```python
from papertrail.store import PaperStore

with PaperStore.open_readonly("papertrail-demo.db") as reader:
    sample = reader.get_sample(1)  # 替换为实际 run_id
```

只读连接持有一次读取事务，正常使用 SQLite 锁和 WAL 一致性机制，没有使用 immutable=1。
关闭 reader 后结束快照；期间其他进程新提交的数据需重新打开 reader 才能读取。
`get_sample(run_id)`（CLI 对应 `--show-run`）返回该 run 的
`paper_ids`、`edges`、`record_counts` 和采集报告。节点为规范化种子与本次记录中的
有效邻居，边只从本次原始记录按 citing → cited 构建，按 ID/有序端点去重。
**不能从 citation_edges 选出“两端均在样本节点中”的边替代本次样本**，那会混入历史关系。

`run_items` 保留重复项和不可解析项，所以 raw_records 与唯一节点/边数不同；缺 ID 行
不会成为节点或边，仍占预算。报告的 `sample_totals` 是当前 run，`database_totals`
是历史累计，二者并列显示。两颗种子之间重复观察到同一条边时，原始记录各计一次，边只计一次。

缓存命中复制已保存的逐条记录前缀；续传先复制前缀，再逐页追加新记录。每页的样本记录、
论文、边和游标同事务提交；旧 run 不随刷新、后续续传、种子变化或预算缩减而改变。
失败/中断的 run 仅包含已提交页与已解析种子，skipped 方向不借用旧缓存边。
查看样本时须检查 status 和报告里的方向状态；`bounded`、`incomplete`、`interrupted`
不能解释成完整覆盖。进程硬终止可能留下 running，仍只能读取已提交的数据。
缓存 checkpoint 同事务保存逐条记录的 SHA-256 摘要；复用前同时校验数量、序号和内容，
删除记录或保持数量不变却替换 ID/缺失标记等变化会使缓存失效并安全重采。
旧 checkpoint 没有摘要时不自动为未知内容补签，将重采一次；只读查看已有 run 不要求
这个新缓存字段，也不会添加它。摘要用于检测记录变化，不是防止同时修改记录和摘要的数字签名。

这里固定的是**样本成员归属**，没有制作论文元数据的历史版本；papers 的标题、摘要、
全局被引数等仍是最新已知值。采集配置和关系记录获取时间已保存，offset 分页仍不是数据源快照。

### 参数和请求安全

| 参数 | 默认 | 允许范围 |
|---|---:|---|
| `--citation-budget` | 150 | 0..1000（全局原始被引记录） |
| `--reference-cap` | 10000 | 1..10000（每篇种子原始参考记录） |
| `--page-size` | 100 | 1..1000 |
| `--request-budget` | 100 | 1..300（本轮请求尝试总数） |
| `--cache-ttl-hours` | 168 | 0..8760 |

CLI 在创建客户端/打开数据库前校验；库入口也校验。请求预算覆盖种子查询、两种方向、
所有分页和重试；每次 HTTP 尝试前扣减，命中缓存不消耗。fixture 同样统计模拟请求，
`request_attempts` 在离线模式不是实际 HTTP 数。耗尽时停止后续请求、保存部分样本，
返回 request_budget 错误、incomplete 和退出码 1。小页/大上限可能先耗尽请求预算；
额度不是完整采集的保证，也不是跨进程累计配额或严格墙钟时限。

### 真实与模拟来源隔离

新数据库绑定 Semantic Scholar 身份或 `fixture:<文件字节 SHA-256>`，两种模式不能
共用数据库；不同内容的 fixture 也不能命中彼此缓存。即使 paperId 相同也会在查询前
拒绝混用，必须另选 DB。复制同一 fixture 到不同路径不改变指纹；改内容/格式会改变指纹。
所有 fixture run 和论文/边始终标为 Synthetic offline fixture；读取 run 使用保存的来源，
不会因为没有传 `--offline-fixture` 而改称真实数据。错误不会自动清库、重新贴标签或回退联网。

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

旧 TASK-002A 数据库打开时增量添加缺失字段和表，保留论文、边及进度，不反推或补造旧 run。
来源全部记录为 Semantic Scholar 的旧库可按该已有声明升级绑定；无法追溯来源的混合/
未知来源库和没有指纹的旧模拟库不自动认领，请保留历史库并使用新 DB。系统不能鉴定此前
被错误标注为真实来源的数据，旧来源标签必须可信。旧外部别名无直接确认来源时仍需确认。
旧关系缓存没有逐条样本归属，即使 TTL 有效也需重取一次，不能把历史并集当作它的样本；
可验证的元数据缓存仍可复用。升级后继续使用原有 TTL/截断缓存/续传规则。
真实分页及 DOI 路径验证仍待授权；NetworkX 基础分析见上文 TASK-002B。

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
失败恢复及独立审查回归。TASK-002A.1 新增 `tests/test_collection_runs.py`，覆盖样本归属、
预算变化、缓存复制、SQL 回滚、来源隔离、请求限制及数据库升级，初版 **119 项离线测试通过**，
包括 TASK-001 的 31 项和 TASK-002A 的 61 项；原有两处手工插入 fixture 的测试显式绑定来源，
保留其业务断言，避免把合成测试数据误标为真实数据。
最终审查补充真正只读、WAL 快照、异常数据库、记录篡改、旧非零游标和参数/报告脱敏回归，
TASK-002A.1 完成时总计 **134 项测试**。--show-run 已验证文件字节与 SQL 内容不变，SQL 跟踪无写操作，
只读连接拒绝 UPDATE。CLI 参数错误及只读报告按当前环境中的 API Key 脱敏；这两个显示路径
可能读取环境变量以隐藏密钥，但不会因此创建 HTTP 客户端或联网。
TASK-002B 初版新增 **58 项离线测试**，当时 **192 项全部通过**。覆盖引用方向、度数、run 隔离、
历史边排除、去重、孤立节点、自环、状态/预算/来源/端点异常、空样本、缺失 run、只读连接、
WAL 一致性及 CLI 摘要/JSON/脱敏。SQL 授权器禁止读历史边表及写入，主动 UPDATE 被拒绝，
分析前后 DB 字节与 SQL 内容一致。原 134 项测试未修改。
TASK-002B.1 在原 192 项基础上按 Owner 新规则更新截断断言，新增 **19 项**，当时
**211 项全部通过**。新增覆盖 5 篇 citing / 配额 4 的截断图、状态与配额伪造拒绝、种子标记、
逐方向 expected_count，以及不设置 PYTHONIOENCODING 时默认重定向、cp936、UTF-8 的 Unicode
标题文本/JSON 输出。完整命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task002b1-pytest-cache
# 211 passed in 28.71s
```

TASK-003 新增 **66 项离线测试**，覆盖候选类别/重叠、种子计数与度数区别、组合筛选、null/0、
并列与缺失排序、全样本统计、跨 run 隔离、合法截断/失败拒绝、只读与 JSON、CLI 编码及脱敏。
原有 211 项测试文件未修改，完整回归 **277 项全部通过**；离线演示的分析前后 DB SHA-256 相同。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task003-pytest-cache
# 277 passed in 53.23s
```

所有数据库测试使用 pytest 临时目录。如果本机旧 `.pytest_cache` 不可写，可指定新的
忽略目录：`python -m pytest -q -o cache_dir=output/task002b-pytest-cache`。

## 官方资料与来源

2026-10-28 核验：[Graph API 文档](https://api.semanticscholar.org/api-docs/graphs)、
[官方 OpenAPI 规范](https://api.semanticscholar.org/graph/v1/swagger.json)、
[官方 API 介绍](https://webflow.semanticscholar.org/product/api)、
[API 使用条款](https://webflow.semanticscholar.org/product/api/license)。
主站部分页面读取返回 403，上述 webflow 子域为同一服务的官方页面。
数据来源标注为 **Semantic Scholar**；数据及第三方内容的使用仍受各自许可约束。
本仓库不提交真实原始响应，不提供数据再分发或商业使用许可判断。
