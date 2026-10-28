# PaperTrail — Project Brief & v0.1 Specification

**Status:** Agreed project scope · 2026-10-28  
**Course:** Python 程序设计课程大作业  
**Project:** PaperTrail — 基于 Python 的科研文献引用关系分析与可视化系统  
**Audience:** Project owner, Codex, Claude Code (CC), future contributors

## 1. Why we are building it

科研初学者进入新领域时，往往只有少数种子论文，却很难快速分辨前期工作、后续发展、值得阅读的候选论文以及不同论文之间的关系。PaperTrail 的目标是：**从 1–3 篇种子论文出发，沿着引用关系收集有限、可追溯的文献样本，生成交互式网络和附有依据的候选阅读清单。**

这是一门 Python 课程的大作业，同时也是计划长期使用和维护的个人 GitHub 软件项目。**课程版本必须独立可运行**；不得把主要功能留到课程结束后才实现。

### Non-goals / 不做什么

- 不做完整文献搜索引擎或替代 Semantic Scholar / Connected Papers。
- 不自动判断“经典论文”“真正 baseline”“最前沿研究方向”，只提供**候选和可检查的依据**。
- 不用有限采集样本冒充全领域统计或完整覆盖。
- 不做 PDF 全文抓取、LLM 自动综述、复杂推荐系统、用户账户、微服务、分布式部署。
- v0.1 不要求自动聚类、第二轮滚雪球、GitHub 仓库核验、多数据源融合。

## 2. Users and core user journey

**用户：** 对 CS/AI/机器人等研究领域有初步兴趣、已经掌握 1–3 篇论文的科研初学者。

**典型流程：**
1. 用户输入 DOI / arXiv ID / 标题（标题搜索须由用户确认唯一匹配结果）。
2. 系统显示种子论文详情，让用户确认。
3. 获取种子的参考文献和有限被引论文，去重、保存，并清晰告知采样、缺失和截断情况。
4. 显示有向引用网络；点击论文节点可以查看论文信息和原始来源链接。
5. 用户根据年份、关键词、全局被引数、**样本中可观察的引用数**等字段筛选、排序；界面解释指标局限。
6. 用户手工勾选论文，导出有来源链接与选择依据的阅读清单。

## 3. v0.1 must-have requirements

| ID | Functional requirement | Minimum acceptance criterion |
|---|---|---|
| F01 | 种子论文导入 | 可通过 DOI、arXiv ID 获取论文；标题检索显示候选而不直接误匹配；支持 1–3 篇 |
| F02 | 单层引用扩展 | 对种子论文读取 API 可提供的全部直接参考文献（需要分页）；向前读取被引论文采用全局 100–200 篇可配置预算（默认 150），明确实际采样策略与截断 |
| F03 | 持久化与缓存 | SQLite 保存唯一论文、引用边、来源/采集时间；再次运行不会不必要地重复请求；可使用预置样例离线演示 |
| F04 | 引用网络 | NetworkX 有向图；定义边 `A -> B` = “论文 A 引用了论文 B”；交互节点能够查看论文详情与来源链接 |
| F05 | 筛选和候选排序 | 年份/关键词/引用数量筛选；显示可解释排序字段。不可将 PageRank 或单一引用数等同于“经典”“baseline” |
| F06 | 年份统计 | 显示**已采集样本**逐年篇数及关键词出现情况，标注数据覆盖与样本局限 |
| F07 | 阅读清单 | 用户选择论文并导出 CSV/Markdown/JSON 之一（首选 CSV + Markdown），含标题、年份、链接、选择理由；图表导出若工期允许 |
| F08 | 可运行、可展示 | Windows 本地按 README 运行；提供离线演示数据；运行过程有基本错误提示 |
| F09 | 项目质量 | 关键逻辑单元测试；`.gitignore` 保护密钥、本地 DB 等；两份课程文档、展示 PPT/演示流程 |

## 4. Optional only if the must-haves are finished

1. 在摘要/元数据中检索 GitHub 代码链接，标记“发现候选链接”，而非“已验证的官方代码”。
2. 引用网络社区发现（例如 Louvain），仅用于探索；避免宣称社区必然等于研究子方向。
3. 对有限候选增补**集合内部**的引用边，不进一步扩张节点；提高局部网络指标可解释性。
4. 小规模二次扩展与新增节点比例实验；必须有明确预算和停止条件。

## 5. Data and methodological constraints

**Primary source:** Semantic Scholar Academic Graph API. **Alternative:** OpenAlex only if first source blocks the MVP and after explicit owner approval. Confirm current API terms, keys, quotas, pagination from official docs before implementation.

- 参考文献“全取”指**数据源能够提供、且请求成功取得的直接参考文献**，不是保证文献世界中所有条目都存在或元数据齐全。
- 被引文献预算默认 150 **全局总数（非每颗种子 150）**；数据请求策略需可重复、可解释，避免声称取的是“最重要的 150 篇”。
- API 返回值可能缺 DOI、摘要、year、author、citation count 或引用边；程序必须容忍空值。
- 引用边方向固定为 `citing_paper_id -> cited_paper_id`；保持数据层和图层一致。
- 单层种子扩展通常产生以种子为中心的“星型”采样网络，候选之间的相互引用边可能未采到。因此**局部被引数、PageRank、社区发现结果均可能有严重采样偏差**。v0.1应显示局限，优先保留简单的可解释指标；需要时增补已有集合内部的引用边。
- `citationCount` 是外部来源的全局引用计数，和“本次采集网络中可观察到的入度”不是一个指标，必须分别命名。
- 关键词/逐年篇数只能说明采集样本，不得据此宣称“领域热度”或“科研趋势”的全貌。
- 所有论文结果应尽量保留 DOI/arXiv/平台来源 URL、来源标记、获取时间，方便核验。
- 缓存过期策略应说明；不在公开仓库中提交个人 API 密钥。

## 6. Architecture and tools (target, not immediate scaffolding requirement)

| Layer | Responsibilities | Likely tools |
|---|---|---|
| Data acquisition | ID parsing, HTTP requests, pagination, retry, rate limit, error messages | `requests`, `S2Client` |
| Storage | unique papers, authors if needed, directed edges, fetched_at, cache | `sqlite3`, `PaperStore` |
| Analysis | filtering, network features, candidate indicators, coverage diagnostics | `pandas`, `networkx`, `CitationAnalyzer` |
| UI / export | seed input, graph, tables, filters, reading list export | `streamlit`, `plotly` or `pyvis` |
| Quality | fast tests, offline fixtures, repeatable install and run | `pytest`, Git/GitHub |

Do not invent one class per feature. Prefer a small `src/papertrail/` package with modules for `api`, `store`, `analysis` and a separate Streamlit app. Introduce complexity only when necessary.

## 7. Self-study / potential bonus-point evidence

The class announcement says the assignment only needs to address a real problem and independent learning is not restricted to specified Django, crawling, front/back-end or database topics. Exact bonus criteria have not yet been supplied.

**Intentional beyond-basic-Python topics:** REST/HTTP API; SQLite relational schema & SQL; directed graphs and network analysis; Streamlit/Plotly interaction; automated testing and version control. Each has a genuine use case, learning record, explanation, screenshot or test, and a cited learning resource. Avoid gratuitous framework additions purely to chase bonus points.

In the course design report include a section **“课外自学技术与应用”**, with a table: technology / why necessary / where implemented / what I learned / evidence (file or demo). Clearly distinguish work learned or independently checked by the owner from AI-assisted code generation.

## 8. Acceptance demonstration

Given 2 user-selected real papers with known references and citations:
1. Successfully import and persist seed metadata.
2. Collect one-hop references/citations within configured budget, report missing/truncated data.
3. Show a clickable directed citation network.
4. Show an explainable table of candidate papers and sample-year distribution.
5. Manually select and export a reading list containing links and reasons.
6. Shut off network access and reopen a sample project from cache or fixtures.
7. Run automated tests; summarize limitations and two to three genuine problems encountered and solved.

**Small validity check:** choose 5–10 relevant papers from a manually curated reading list; see which appear in the sampled graph and how they rank. Report the exact small-scale retrieval rate and selection bias; **do not equate this with overall search recall**.

## 9. Timing: current week 8, not week 6

- **Oct 28–Nov 2:** Milestone 0 (API probe, data shapes, fixtures, project setup).
- **Nov 3–9:** Milestone 1 (metadata, one-hop crawl with bounds, SQLite/cache, basic tests).
- **Nov 10–16:** Milestone 2 (NetworkX graph, candidate fields, filtering, data coverage diagnostics).
- **Nov 17–22:** Milestone 3 (Streamlit minimum UI, visualization, reading-list export, offline demo). **Feature freeze Nov 22.**
- **Nov 23–29:** Milestone 4 (tests, polishing, case study, README, course design report and user manual).
- **Nov 30–Dec 5:** rehearsal, slides, submission and three-party confirmation. Target finishing before week 14 begins on **Dec 7**; confirm official BB deadlines with teacher.

There is no time commitment to optional features until all must-haves pass. Protect time for other course obligations.

## 10. Course deliverables

- Standardized Python source with functions / suitable classes;
- Data/fixtures and operation instructions;
- **《课程设计说明书》:** title, purpose, design ideas, key techniques, self-evaluation, sources, plus architecture and test evidence;
- **《软件使用说明书》:** project title, functions, installation, operating examples, screenshots and errors;
- Classroom presentation and live/offline demo;
- Submission on BB and teacher/student representative/self confirmation, as specified by class.

## 11. References to consult

- Semantic Scholar Academic Graph API docs: https://api.semanticscholar.org/api-docs/graphs
- Semantic Scholar API portal: https://www.semanticscholar.org/product/api
- OpenAlex Help Center: https://help.openalex.org/api/
- Streamlit multipage apps: https://docs.streamlit.io/develop/concepts/multipage-apps/overview
- NetworkX docs: https://networkx.org/documentation/stable/

## 12. Decision policy

The project owner owns scope, trade-offs, external API registration, code merge/push, licensing decisions and release acceptance. AI agents may propose changes but must not silently expand the requirements. If an external API is unavailable, preserve a functional offline demo and report the blocker honestly.
