# PaperTrail
A Python tool for exploring academic citation networks, analyzing research literature, and building explainable reading lists.

当前仅实现 **TASK-001：Semantic Scholar API 可行性验证**，不是完整产品。
范围与实测记录见 [项目简报](docs/PROJECT_BRIEF.md) 和 [任务记录](tasks/TASK-001-API-SPIKE.md)。

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

## API Key 与运行

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

两种入口均从仓库根目录运行，`pytest.ini` 的 `pythonpath = .` 用于发现项目模块。
8 组核心场景通过参数化覆盖：成功解析/字段/方向/去重/限速、空值与分页、
CLI/无密钥/单端点/保存脱敏、非重试 HTTP 错误、429/5xx/Retry-After、
SSL/超时/连接错误、坏响应/部分成功、写盘失败。网络及等待均被模拟，
测试不会读取真实密钥或发出 API 请求。
`tests/fixtures/s2_spike.json` 是明确标注的**合成数据**，不是线上响应。

## 官方资料与来源

2026-10-28 核验：[Graph API 文档](https://api.semanticscholar.org/api-docs/graphs)、
[官方 OpenAPI 规范](https://api.semanticscholar.org/graph/v1/swagger.json)、
[官方 API 介绍](https://webflow.semanticscholar.org/product/api)、
[API 使用条款](https://webflow.semanticscholar.org/product/api/license)。
主站部分页面读取返回 403，上述 webflow 子域为同一服务的官方页面。
数据来源标注为 **Semantic Scholar**；数据及第三方内容的使用仍受各自许可约束。
本仓库不提交真实原始响应，不提供数据再分发或商业使用许可判断。
