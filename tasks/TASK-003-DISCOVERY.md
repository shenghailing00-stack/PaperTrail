# TASK-003 — 候选论文发现、筛选、排序与样本统计

**状态：** 实现与离线验证完成，等待 Owner 验收。  
**日期：** 2026-11-10（America/Los_Angeles）。  
**主要实现者：** Codex，唯一编辑者，未启动子代理。  
**边界：** 无 Streamlit、新 API、PageRank、社区发现、LLM、真实 API、Commit 或 Push。

## 开发前检查、设计与验收

已读 AGENTS.md、docs/PROJECT_BRIEF.md、README.md、analysis.py、analysis_cli.py、既有图分析测试及相关存储代码。
工作区初始 `git status --short` 为空。现有 `.venv` 为 Python 3.11.7；复用已安装 NetworkX，未安装依赖。
原有测试基线 **211 passed in 42.17s**。已有 Git safe.directory 配置警告未改动。

先向 Owner 说明文件、算法、空值/筛选/排序规则及验收方案，再实施。
复用 `load_graph` 作为数据库到分析的唯一入口，继承 TASK-002B.1 的状态检查；不改采集或存储层。
不改旧图分析接口与其种子入度诊断表，新增候选入口专供阅读清单与后续界面使用。

验收覆盖：两类候选与重叠、去重种子计数、入度/出度、默认排除种子、组合筛选、null 与 0、
稳定并列、run 隔离、合法截断/失败拒绝、序列化、只读、CLI/编码/脱敏，最后回归原有 211 项。

## 修改文件与职责

| 文件 | 职责 |
|---|---|
| `src/papertrail/discovery.py` | 候选发现、独立筛选/排序/统计函数，`discover` 与 JSON 结构 |
| `src/papertrail/discovery_cli.py` | 参数校验、简洁文本与 JSON、编码处理及脱敏 |
| `scripts/discover.py` | 仓库内直接运行入口 |
| `tests/test_discovery.py` | 临时 SQLite 与合成 fixture 的离线验收 |
| `README.md` | 运行命令、字段与空值语义、限制及后续界面接口 |
| 本文件 | 设计、证据、权衡及交接 |

没有修改原有测试、fixture、依赖、analysis/store/collector/api 模块，也未修改用户现有数据库。
测试在临时目录建库，手工示例在 Git 忽略的 `output/` 下另建演示库。

## 算法、接口与规则

对当前图中每个节点 p，令 S 为当前 run 的种子集合：

- `referenced_by_seed_ids = predecessors(p) ∩ S - {p}`，即实际观察到 `seed → p` 的不同种子。
- `cites_seed_ids = successors(p) ∩ S - {p}`，即实际观察到 `p → seed` 的不同种子。
- 非种子前者非空属于 `shared_reference`，后者非空属于 `shared_citing`。至少一篇种子即可，允许重叠。
- `local_in_degree/out_degree` 仍是当前图中所有相应边的数量；集合交集后的种子数量与总度数不是同一概念。
- 自环保留在度数中，但不把自己算作“另一篇种子”。种子有独立 seed 依据，不计入两类非种子候选数。

每行保留元数据和来源，以及 `categories`、两组种子 ID/数量、局部度数、`evidence` 和 `reason`。
结构化 evidence 包含类别、种子 ID、种子数与边方向，可直接检查图中的有向边。
“共同后续研究”仅为引用方向类别，不根据缺失/异常年份推断时间先后。

筛选不裁剪图，故结果中的度数始终相对于原始当前样本。范围含端点、可单侧设置；启用某范围时
排除该字段 null，未启用则保留。关键词 casefold 后做字面子串搜索，标题/摘要满足其一即可；
多个关键词为 AND，多类别为 OR，不同筛选条件为 AND。不引入分词、正则或 ML 依赖。
种子没有候选类别，类别筛选会排除种子；`include_seeds=True` 只允许显示满足其余条件的种子。

四个排序字段：`referenced_by_seed_count`、`cites_seed_count`、`global_citation_count`、`year`，均降序。
同值只按 paperId 升序保证可复现，无研究价值含义；没有隐含的第二评分指标。
并列名次为 1/1/3，`tie_count` 含自身、按筛选后且 top 前的每组计算。null 放末尾，不赋予排名，
`rank=None, tie_count=0`；已知 0 仍是正常测量值。种子始终单独排序，混合调用排序函数会报错。

统计覆盖筛选前全部当前样本：唯一论文总数、种子/非种子数、两类及重叠候选数、年份分布、
年份/标题/摘要/全局引用量/链接/作者/外部 ID 缺失数。缺年份单独报告，不填 0 年。
关键词统计按唯一论文而非词频计数；任一字段计数为标题/摘要命中集合的并集。
每个词独立统计，与多关键词 AND 筛选的交集口径不同。数值 0 不计为缺失。

稳定接口：

```python
from papertrail.discovery import discover, summarize_discovery

result = discover(db_path, run_id, keywords=["graph"], categories=["shared_citing"],
                  year_min=2020, citation_min=0, sort_by="cites_seed_count", top=None)
# 如果已有 load_graph() 返回的未修改图：
result = summarize_discovery(graph, include_seeds=True, sort_by="year")
```

输出 `schema_version=1` 和 `sample/statistics/selection/ranking/candidates/seeds`。
Python 默认 top=None 返回全部匹配行；CLI 默认 top=5、允许 1..100，JSON 同样遵循 top。
返回值是独立可 JSON 序列化的字典/列表，调用者修改结果不会修改源图。
后续 Streamlit 可直接使用候选表、独立种子表和样本统计，同时展示 sample 内警告/局限。

## 数据安全、限制与设计取舍

1. **一个函数模块而非类层级：** 发现/筛选/排序/统计各有明确函数，同时复用现有图加载边界；没有为结构拆出大量文件。
2. **一个主排序指标：** 易检查，保留大量同分；ID 只决定展示位置，不能解释成“更值得读”。
3. **统计不随筛选改变：** 避免把过滤后的数量误称为整个采集样本；另给筛选后/top 前总数供 UI 显示。
4. **旧入口保留兼容：** TASK-002B 的诊断结果不变，TASK-003 是新的候选表入口，默认排除种子。

只读加载复用 get_sample(run_id) 的关系，不查询历史边表补边。支持 complete 和合法 budget_truncated，
拒绝失败、不完整、不一致样本；保留来源、配额、截断方向、原始覆盖及采样警告。
元数据读取采用已有只读快照，字段仍为最新已知值，而非 run 采集时的历史版本。
单层 API 顺序采样可能漏掉非种子之间的引用，出度不是完整参考文献数，不能由此认定经典、baseline 或热点。
统计仅代表当前采集样本；fixture 完全是合成数据。本任务没有验证真实 API 分页、召回率或推荐效果。

## 测试与离线演示证据

运行命令（仓库根目录、现有虚拟环境）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py -q -o cache_dir=output/task003-pytest-cache
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=output/task003-pytest-cache
```

新增测试首轮为 62 通过、2 失败：短语 `graph methods` 实际匹配 C/D，测试预期漏了 C；
只读连接测试包装函数形参 `uri` 与 SQLite 关键字 `uri=True` 重名。均修正测试本身，
没有放宽功能边界。增加错误列表参数校验后，新增 **66 passed in 14.66s**。
最终完整回归 **277 passed in 53.23s**（原有 211 项 + 新增 66 项），原有测试文件未修改。
`git diff --check` 通过，工作区没有合并冲突；仅本任务声明的六个文件发生新增或修改。

只读验收包括：SQLite mode=ro 拒绝主动 UPDATE；禁止调用可写 store 构造器；SQL 授权器禁止
读取 citation_edges 和执行写入；记录的 SQL 只有 BEGIN/SELECT/PRAGMA；分析前后 DB 文件字节与 SQL dump 相同。
HTTP 请求及客户端构造在新增测试中被禁止；编码测试通过子进程验证默认、cp936、UTF-8 输出。

已实际运行原有 `s2_collection.json` 的完整与截断示例，调用真实 CLI（仅本地进程，无网络），
文本与 JSON 均成功，分析前后数据库 SHA-256 一致。完整 JSON、文本和演示库保存在：
`output/task003-demo-3_dh1k9s/`，此目录被 Git 忽略。

| 合成论文 | 类别 | 被不同种子引用数 | 引用不同种子数 | 局部入度 / 出度 |
|---|---|---:|---:|---|
| C — Shared synthetic reference | 共同参考文献 | 2（A/B） | 0 | 2 / 0 |
| D — Synthetic citing paper | 共同后续研究 | 0 | 2（A/B） | 0 / 2 |

完整示例 run_id=1，4 篇论文、2 篇种子、2 篇非种子；两类各 1，无重叠。
2024/2025 各 1 篇，缺年份 2、缺摘要 3、缺全局引用量 2；`synthetic` 命中 4 篇标题。
非种子年份/全局引用量均未知，启用这些范围筛选会得到空候选，这是预期行为。
单种子引用预算 1 的另一独立示例也是 run_id=1：3 篇论文，两候选各关联 1 篇种子，
采集范围为 budget_truncated，保留原始截断方向、expected_count 和警告。

## Owner 学习与下一步

- 阅读 `discover_papers`：前驱/后继决定引用方向，集合交集限制到种子，不能把入度误认作“引用了多少篇种子”。
- 阅读 `filter_papers` 和 `rank_papers`：None 与 0 的区别，过滤条件组合，单指标稳定排序与并列排名。
- 阅读 `summarize_discovery`：先对完整样本统计，再筛选、分组排序，最后组成供界面使用的 JSON。

手动核对：对完整示例运行 `--category shared_citing --sort-by cites_seed_count`，应仅看到 D；
再加 `--citation-min 0` 应为空。解释为什么“未知引用量”不能当成 0。

下一阶段由 Owner 验收后决定 Streamlit 任务；本阶段不实现界面、导出阅读清单或任何后续功能。
