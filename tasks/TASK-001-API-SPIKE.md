# TASK-001 — Semantic Scholar API feasibility spike

**Owner:** Human product owner  
**Implementer:** Codex  
**Reviewer:** Claude Code  
**Type:** timeboxed proof of concept  
**Deadline:** Oct 28–Nov 2, 2026

## Goal

Verify that a minimal Python program can retrieve **one** real paper's metadata, first page of references, first page of citations, and save sanitized JSON responses locally. This task is about learning the API and understanding actual data shapes, **not** building the product.

## Before coding

- Read `docs/PROJECT_BRIEF.md`, `AGENTS.md`.
- Check the official Semantic Scholar endpoint docs, supported IDs/fields, pagination, and key/rate limit documentation.
- Explain proposed minimal file layout, API calls, credentials handling and success/failure criteria in 5–10 lines.
- Use a public sample identifier such as `ARXIV:1706.03762` *only as an example*; accept a CLI parameter and avoid hardcoding business logic to one paper.
- Owner supplies 2–3 candidate demo seed papers (preferably arXiv/ACL/NeurIPS-type sources with good S2 coverage) for the coverage check below.

## In scope

- Python 3.11+ environment instructions; minimal dependencies (`requests`, `pytest`; `python-dotenv` only if truly necessary).
- Simple CLI `python ... <paper-id>` or equivalent with 3 requests: paper details, references with `limit=5`, citations with `limit=5`.
- Reference and citation requests also ask for the citation-level fields `contexts`, `intents` and `isInfluential` (confirm exact names in the official docs). These come in the same request at no extra cost and are the intended evidence for later "selection reason" output; record how often they are empty.
- Report unresolved items: references/citations whose `citedPaper`/`citingPaper` has `paperId: null` are counted separately as "unresolved", never silently dropped.
- Configurable API key through environment variable; graceful behavior without a key if the API permits.
- Explicit timeout and bounded retry handling for 429/5xx; distinguish invalid ID, authentication, network timeout and empty output.
- JSON output in Git-ignored `data/raw/`; display concise counts, missing fields and `next` pagination indicators.
- At least 3 offline pytest cases: success parsing; `null` or empty data; API failure (mock network).
- Tiny deterministic fixture(s) in `tests/fixtures/`, safe to keep in Git; no secret values; trim `contexts` to short snippets.

## Out of scope

No SQLite, Streamlit, NetworkX, recommendation, full pagination harvest, second source integration, paper clustering, GitHub publishing or broad refactoring.

## Acceptance checklist

- [x] Setup instructions work in a fresh Python environment on the current Windows machine; owner-machine reproduction remains to be confirmed.
- [x] Metadata request responds with paperId/title/year/author/url when available, and documents missing fields.
- [x] References have `citedPaper`; citations have `citingPaper`; printed examples explain `citing -> cited` direction.
- [x] First 5 reference and first 5 citation items are saved to local JSON (or exact empty/error status is reported).
- [x] Output shows `contexts`/`intents`/`isInfluential` for each reference/citation item, or states that they are empty/unavailable.
- [ ] **Owner-approved deferred — REALM/RAG/DPR additional coverage checks.** SSL/429 blocked verification; this item is deferred, not passed, and no longer blocks TASK-001 acceptance. Attention/Diffusion Policy results and the REALM failure evidence remain below. No further live retries for these seeds in TASK-001.
- [x] `pytest` offline suite passes; live status is stated separately, honestly.
- [x] API key is neither committed nor printed.
- [ ] Human owner can point to the request function and explain JSON parsing and exception handling.

## Demo seeds (owner-selected 2026-10-28)

**最终验收范围调整：Owner-approved deferred。** 所有者正式批准将 REALM/RAG/DPR
额外覆盖检查延期，原因是 SSL/429 阻塞；不再为 TASK-001 反复请求受限 API。
以下预检数字与后续实测/失败证据保留，延期不等于已通过。
本决定取代下方历史记录中要求在 TASK-001 内重新验证这些种子的安排。

Retrieval-augmented generation group. Use the first two for the 2-seed acceptance demo; DPR is the third seed.

| Seed | ID | Global citations* | References returned / resolved* | With `contexts`* |
|---|---|---|---|---|
| REALM | `ARXIV:2002.08909` | 3,563 | 43 / 42 | 41 |
| RAG | `ARXIV:2005.11401` | 20,138 | 67 / 67 | 64 |
| DPR | `ARXIV:2004.04906` | 7,494 | 55 / 54 | 24 |

\* Pre-check by Claude Code on 2026-10-28 (one references page, `limit=1000`, with API key); 31 resolved references are shared by ≥2 of these seeds. TASK-001 must re-verify with its own code. Observations from the same check across 12 candidate papers:

- References can contain duplicates (e.g. GCN `referenceCount` 39, 40 items returned), so counts must be deduplicated by `paperId`.
- `intents` can be nearly empty even when `contexts` exist (e.g. Latent Diffusion 2/111); treat `contexts` as the primary evidence.
- Up to ~9% of references are unresolved (`paperId: null`).
- Even with a key, retries happened (24 requests took >6 min); log the reason (429 vs. connection error) for every retry.

## Required handoff

Show changed paths, commands to run, output example with sensitive values redacted, tests run and status, API limitation notes and 3 short learning questions. Stop when acceptance is met; do not pre-implement TASK-002.

## 批准后的范围调整（2026-10-28）

- 运行/测试方法放 README，本文件记录结果，不创建重复任务文档。
- 三个端点均显式指定 fields，包含摘要与引用计数；引用端点额外请求关系证据。
- 独立 `--references-limit` / `--citations-limit`，默认 5、范围 1..1000。
- `--references-only` 只请求一页参考文献（最多 1000），不自动翻页。
- 当时相邻请求含重试至少间隔 1.1 秒（TASK-005.1 已调整为 2.0 秒）；每个端点最多 3 次尝试，匿名第二次 429 停止。
- 离线验证先于真实请求，8 组核心场景；不提前实现 TASK-002。

## 执行结果（2026-10-28，Asia/Shanghai）

**本轮批准范围的实现与两篇论文验证完成，等待所有者复核及代码讲解验收。**
本轮显式指令指定 Attention Is All You Need、Diffusion Policy，因此实际验证限于这两篇。
上方 Demo seeds 中 REALM/RAG/DPR 的独立预检记录原样保留；本轮没有重新请求这三篇，
不能将其预检数字视为本程序的实测结果，对应扩展演示种子的验收项保持待完成。
本地新建 `.venv`，Python 3.11.7；安装 requests 2.34.2、pytest 8.4.2。
8 组核心测试参数化为 25 个用例，首次运行结果为 `25 passed in 0.71s`。
`pip check` 通过；`git diff --check` 通过。
依赖清单采用兼容版本范围，以上版本是本次实际安装值，不是完整依赖锁定。

新增：`scripts/api_spike.py`、`requirements.txt`、`tests/test_api_spike.py`、
`tests/fixtures/s2_spike.json`；修改：`README.md`、本任务文档。
现有 `.gitignore` 已覆盖 `.venv`、`.env`、缓存与 `data/raw/`，未修改。
没有提交、推送或修改项目简报；没有新增数据库、图分析或 UI。

### 官方文档核验

查阅 [官方 Graph API 规范](https://api.semanticscholar.org/graph/v1/swagger.json)：
ARXIV/DOI 等前缀 ID 可用于 paper 端点；引用端点的 limit 上限为 1000，
嵌套论文字段直接写 `title,abstract,...`，不加 citedPaper/citingPaper 前缀；
`next` 缺失按协议表示没有后续数据。请求包含显式 fields，关系字段为
contexts、intents、isInfluential。缺少 next 仍不证明现实参考文献完整覆盖。

[官方 API 介绍](https://webflow.semanticscholar.org/product/api) 说明匿名访问
共享限流资源、初始 Key 速率为 1 RPS；当时程序统一采用 1.1 秒起始间隔（TASK-005.1 当前为 2.0 秒，下方实测时间保留历史原值）。
[官方使用条款](https://webflow.semanticscholar.org/product/api/license) 已查阅，
报告保留 Semantic Scholar 来源标注，真实响应仅留本地，不对再分发许可作推断。
原主站介绍/许可页面通过浏览工具返回 403，使用官方 webflow 子域读取。

### 实际执行的主要命令

以下命令均在仓库根目录运行。安装和 API 访问使用获准的沙箱外执行；
普通验证的终端输出与 JSON 均脱敏，未传入或打印密钥字面值。

```powershell
& 'D:\python\python.exe' --version
& 'D:\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --retries 1 --timeout 20 --disable-pip-version-check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:1706.03762
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:2303.04137
.\.venv\Scripts\python.exe scripts/api_spike.py 204e3073870fae3d05bcbc2f6a8e263d9b72e776 --references-only --references-limit 1000 > data/raw/attention-references-console.txt
.\.venv\Scripts\python.exe scripts/api_spike.py bdba3bd30a49ea4c5b20b43dbd8f0eb59e9d80e2 --references-only --references-limit 1000 > data/raw/diffusion-references-console.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip freeze
git diff --check
git status --short
git check-ignore .venv/Scripts/python.exe data/raw/20261028T103104886935Z.json
```

补充只读检查使用 PowerShell 解析本地 JSON，统计请求间隔和缺失情况。
普通模式实际相邻请求起始间隔为 1.101804、1.106912、3.799736、1.112035 秒。
扫描本轮源文件、fixture、文档及本地 JSON/控制台输出，未发现所配置的 API Key 值。
先运行第一篇成功，再运行第二篇；之后各做一次参考文献覆盖检查。
**论文 API 共 8 次实际请求，全部 HTTP 200，0 次重试；不包括文档读取。**
普通模式各 3 次、覆盖模式各 1 次。没有自动翻页，也没有为测试制造线上错误。

### 真实数据与质量统计

时间：2026-10-28 18:31–18:33（北京时间）。所有比率分母是该次返回条目数，
不是全领域论文数；大页检查包括不可解析条目，不把它们悄悄丢弃。

| 论文 | 元数据年份 | 作者数 | referenceCount | citationCount | 请求的元数据缺失字段 |
|---|---:|---:|---:|---:|---|
| Attention Is All You Need | 2017 | 8 | 41 | 198835 | 无 |
| Diffusion Policy | 2023 | 8 | 71 | 4938 | 无 |

citationCount 是来源的全局计数，不是此次采样网络入度，并会随数据更新变化。

| 论文 / 请求 | 返回数 | 可解析 / 未解析 | 摘要缺失 | contexts 非空 | intents 非空 | next / 截断 |
|---|---:|---:|---:|---:|---:|---|
| Attention / references limit=5 | 5 | 5 / 0 | 0 | 4/5 | 4/5 | 5 / 是 |
| Attention / citations limit=5 | 5 | 5 / 0 | 4 | 0/5 | 0/5 | 5 / 是 |
| Diffusion Policy / references limit=5 | 5 | 5 / 0 | 0 | 4/5 | 1/5 | 5 / 是 |
| Diffusion Policy / citations limit=5 | 5 | 5 / 0 | 3 | 2/5 | 0/5 | 5 / 是 |
| Attention / references limit=1000 | 41 | 39 / 2 | 8 | 32/41 | 29/41 | 缺失 / API 报告末页 |
| Diffusion Policy / references limit=1000 | 71 | 66 / 5 | 20 | 60/71 | 5/71 | 缺失 / API 报告末页 |

- 六个关系响应都不是空列表；已解析条目内重复数均为 0。
- isInfluential 每条都有布尔值；false 作为有效返回计数，不等同于缺失。
- 本次 contexts/intents 未见 null 或缺字段；表中非空以外的条目均为空列表。
- 大页 Attention 的其他缺失：paperId/externalIds/authors/url/referenceCount/citationCount
  各 2 条，year 1 条；Diffusion Policy 对应字段及 year 各 5 条。两者 title 均非空。
- 大页返回数分别等于之前元数据的 referenceCount；没有额外核对论文 PDF，
  因此只能确认本次 API 结果吻合，不能宣称参考文献无遗漏。
- 两篇可用于基本数据获取与引用方向演示。Attention 更适合展示已有引用意图；
  Diffusion Policy 的 intents 仅 5/71，**不适合演示高覆盖的引用意图说明**。
  两篇被引首屏上下文较稀疏，不保证每条引用都能生成证据说明。

本地证据（均被 Git 忽略）：

- `data/raw/20261028T103104886935Z.json`：Attention 三端点。
- `data/raw/20261028T103146457465Z.json`：Diffusion Policy 三端点。
- `data/raw/20261028T103240058342Z.json`：Attention 单页参考文献覆盖。
- `data/raw/20261028T103259734810Z.json`：Diffusion Policy 单页参考文献覆盖。

输出摘要示例（取自 Attention 大页真实报告，未包含认证信息）：

```json
{
  "returned": 41,
  "resolved": 39,
  "unresolved": 2,
  "empty_list": false,
  "next_present": false,
  "next": null,
  "pagination": "end_reported_by_api",
  "truncated": false
}
```

### 遇到的问题及处理

1. `py -0p` 只列出 Python 3.9；进一步检查 PATH 上的解释器找到
   `D:\python\python.exe` 3.11.7，使用其创建虚拟环境，没有安装新的 Python。
2. 沙箱内文档请求及首次 pip 安装被网络权限阻止（pip 报 WinError 10013）；
   使用已获准的沙箱外执行后成功。不是 Semantic Scholar 服务故障。
3. 一次辅助密钥扫描的 Python 单行命令因 PowerShell 引号传递出错，未运行扫描；
   改用原生 PowerShell 做只返回布尔值的扫描，不显示密钥内容。
4. Git 输出已有全局 safe.directory 配置警告；仓库命令成功，未修改全局 Git 配置。

### 设计取舍、限制与待确认项

- 保持单脚本，HTTP、纯解析和流程编排分别用函数隔离；不搭建产品包结构。
- 原响应保留、摘要另外统计；不以去重或丢弃未解析项美化覆盖率。
- 按官方协议识别缺少 next，同时区分数据源覆盖与采样截断；异常值用未知状态。
- 合成 fixture 稳定且短小，不将真实响应提交 Git；离线通过不冒充线上成功。
- SSL/429/超时/错误 ID/空响应均有离线模拟验证；首轮两篇线上全部成功。
  后续协作审计轮实际遇到 SSL 与 429（见下节），匿名访问、超时、错误 ID、空响应
  仍未在线刻意触发。
- Windows 当前机器的新虚拟环境验证成功；未在另一台干净 Windows 11 机器复现。
- 限速/预算只在单个进程内共享；请串行运行。没有实现缓存、并发、多种子采集或翻页。
- 尚待所有者阅读代码、手工核验引用方向与学习验收；独立审查者可只读复核此改动。

重点阅读的三个函数：

1. `S2Client.request_json`：请求、限速、超时、有限重试和安全错误信息。
2. `summarize_relations`：引用方向、未解析项、缺失字段和分页状态，完全不联网。
3. `run_probe`：三个端点的先后关系、稳定 ID、单独参考文献模式与部分失败保留。

三个学习问题：

1. 从一份本地 references 响应选一条已解析记录，能否指出种子与 citedPaper，
   并解释为什么边是“种子 → citedPaper”？
2. 为什么 Attention 大页的 returned 是 41，而可构造的唯一引用边只有 39？
3. 缺少 next、`data: []`、`data: null` 和 HTTP 429 分别意味着什么？

下一步是所有者复核本任务，不自动开始 TASK-002。

## 协作状态与交付审计（2026-10-28，后续轮次）

所有者确认 Claude Code 已完成独立数据源预检，今后只读审查。
Codex 是本次实现的唯一编辑者。本轮没有修改 Python 代码、依赖或测试，
只复核已有实现并更新本文件；没有执行 commit、push、reset、restore 或进入 TASK-002。

### 测试与真实 API 补验

- 重新执行 `.\.venv\Scripts\python.exe -m pytest -q`：**25 passed in 0.72s**。
- `.\.venv\Scripts\python.exe -m pip check`：`No broken requirements found.`
- 前一轮 Attention/Diffusion Policy 的 4 份 JSON 已重新读取核对，保持前述
  8 次 HTTP 200 的历史事实；本轮没有重新抓取这两篇，也没有将历史结果标为新请求。
- 按 Commit 中的要求尝试用本程序补验 REALM，再视结果继续 RAG/DPR。
  两次 CLI 均启用密钥、TLS 校验和已有请求预算；控制台输出保存在忽略目录：

```powershell
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:2002.08909 --references-limit 1000 > data/raw/realm-verification-console.txt
.\.venv\Scripts\python.exe scripts/api_spike.py ARXIV:2002.08909 --references-limit 1000 > data/raw/realm-verification-recheck-console.txt
```

| 运行 / 北京时间 | 实际结果 | 依赖端点 | 本地 JSON |
|---|---|---|---|
| 首次 / 18:57 | 元数据请求 SSL 错误，无 HTTP 状态码；1 次尝试，退出码 1 | references/citations 均 skipped | `data/raw/20261028T105715838177Z.json` |
| 受控复核 / 19:03–19:04 | 元数据 429、429、200；references 429、429、429；6 次尝试，退出码 1 | citations 因 rate_limit 而 skipped | `data/raw/20261028T110410361765Z.json` |

首次 SSL 错误后，做了一次不携带 API Key 的官方 swagger.json 连接诊断，返回
HTTP 200；据此进行上表的一次受控复核。该诊断不能确定首次 SSL 的具体根因，
没有通过关闭证书校验解决，也没有修改代理、证书或系统配置。

本轮论文请求尝试 **7 次：1 次 SSL 失败、5 次 HTTP 429、1 次 HTTP 200**。
复核运行内部共 4 次自动重试，每个端点的退避为 2、4 秒；参考文献耗尽
3 次尝试后停止。另有 1 次无密钥文档诊断，不计入论文请求数。
至此本程序两轮合计 15 次论文请求尝试，其中 9 次 HTTP 200。
连续限流后没有再启动 RAG 或 DPR；这三篇的引用覆盖验收仍未完成，
不能用 Claude 的预检数字代替本程序的测量值。

### Git 状态、Commit 兼容性与重复检查

本地分支 `main`，HEAD 为 `67251eb`；相对本地已有的 `origin/main` 引用 ahead 2。
未执行 fetch，因此该差异不代表对远端实时状态的联网确认。

| 分类 | 文件 / 状态 |
|---|---|
| 已提交：`8c9f445` | `.gitignore`、`AGENTS.md`、`CLAUDE.md`、`START_PROMPTS.md`、`docs/PROJECT_BRIEF.md`、`tasks/TASK-001-API-SPIKE.md` |
| 已提交：`67251eb` | TASK-001 文档中的 REALM/RAG/DPR 演示种子和预检记录 |
| 已跟踪、未暂存 | `README.md`、`tasks/TASK-001-API-SPIKE.md` |
| 已暂存 | 无 |
| 未跟踪、待验收 | `requirements.txt`、`scripts/api_spike.py`、`tests/test_api_spike.py`、`tests/fixtures/s2_spike.json` |
| 本地生成且忽略 | `.venv/`、测试缓存、`data/raw/` 中 JSON 和控制台输出 |

执行了 `git status --short --branch`、两个 Commit 的 `git show`、
`git diff --name-status`、`git diff --cached --name-status`、
`git ls-files --others --exclude-standard`、`git ls-files -u` 和差异检查。
没有未合并索引条目、冲突标记或重复 Python 实现。
同一路径的修改交集只有任务文档：本地更新验收状态并追加实现证据；
从 HEAD 提取的整个 Demo seeds 段落仍逐字存在于工作文件，未覆盖 Claude 的记录。
`.gitignore` 复用已提交规则；未创建重复任务文档。

### 敏感信息与数据审计

只读扫描覆盖本地可达的 3 个 Commit / 15 个历史 blob、7 个索引文件、
11 个已跟踪或未跟踪工作文件，以及本地忽略目录内的 10 个输出文件。
扫描所配置密钥的 UTF-8 / UTF-16 值，以及私钥块、常见 GitHub/AWS 凭据、
带认证信息的 URL、疑似硬编码密钥赋值；仅输出命中位置和类别，不打印值。
**未发现命中**；固定测试用 `test-secret` 是合成哨兵，不是真实凭据。

`git ls-files` 中没有 `.env`、`.venv`、数据库、私钥或 `data/raw/` 文件；
索引没有新增文件。测试 fixture 明确标为合成数据，真实响应未被纳入版本控制。
任务文档中的实测统计摘要为有意记录的验收证据，不是原始响应数据集。
该结果限于本地可达历史与所述扫描规则，不是对任意未知秘密的绝对保证。

### 遗留问题与所有者验收

1. REALM/RAG/DPR 额外覆盖检查为 **Owner-approved deferred**：SSL/429 阻塞，
   既有证据保留，不标记通过，不再作为 TASK-001 本轮待执行的联网操作。
2. 请在自己的 PowerShell 中按 README 确认虚拟环境、运行离线测试；
   未在另一台干净 Windows 11 机器复现。
3. 请手工查看 Attention 大页的一条已解析边和一条未解析记录，解释
   returned=41 与 unique_edges=39 的差别，并阅读 request_json、summarize_relations、run_probe。
4. Claude 可只读审查本次未提交代码；最终验收和是否暂存/提交由所有者决定。
5. Git 仍提示既有全局 safe.directory 配置警告及 LF/CRLF 提示，未影响检查，
   没有修改全局 Git 或换行配置。

本轮交付后停止修改，等待所有者验收。

## 最终审查修复（Owner 限定范围，仅离线）

- 新增 `pytest.ini`，设置 `pythonpath = .`，统一模块入口与 pytest 可执行文件的导入路径。
- `scripts/api_spike.py` 对明确的暂时性 SSL EOF/连接重置/中止复用原有
  最多 3 次尝试和 2、4 秒退避；识别嵌套证书验证异常，证书失败或未知 SSL 错误不重试。
  不打印底层异常文本，保持 TLS 校验；既有 JSON 的 `error.kind = ssl` 保持兼容。
- 终端只显示端点质量摘要、运行/错误状态与最多 3 条引用边，优先兼顾两种方向。
  示例显示两端 ID/title/year；缺失值为 null，不为补标题增加请求。
  完整 JSON 继续保存全部条目及关系证据，原字段不删除，条目新增 title/year。
- `tests/test_api_spike.py` 在原 8 组核心场景中补充 SSL 中断恢复/耗尽、证书失败、
  TLS 校验和重试限速、输出上限、标题年份与引用方向、完整 JSON 保留的断言。
- README 删除个人解释器绝对路径，保留可复现的 Windows 相对路径示例，
  补充两种 pytest 入口及更新后的错误处理和终端输出说明。

本轮不安装依赖、不进行真实 API 请求，也不重写既有实测数据；
没有执行 Git commit/push，不进入 TASK-002。

离线验证结果：

- `.venv\Scripts\python.exe -m pytest -q`：**31 passed in 0.83s**。
- `.venv\Scripts\pytest.exe -q`：**31 passed in 0.60s**。

本轮实际编辑文件仅为 `pytest.ini`（新增）、`scripts/api_spike.py`、
`tests/test_api_spike.py`、`README.md` 和本任务文档；未改动 fixture 或依赖清单。
SSL 新策略仅经离线模拟验证，不将其描述为已通过真实 API 验证。
REALM/RAG/DPR 保持 Owner-approved deferred，等待所有者验收本次修复。
