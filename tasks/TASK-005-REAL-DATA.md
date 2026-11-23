# TASK-005 — 真实 Semantic Scholar 数据端到端验证

**状态：** 🟡 **基本完成（有限制）**。已用新 Key 取得真实样本 run 5（2 篇种子，`bounded`，60 篇论文 / 60 条边），离线分析、候选发现、UI、导出、缓存复用和标题搜索均已实测通过。未完成：第 3 篇种子 Robot Synesthesia 的关系数据（S2 间歇性 429）。详见文末"解除阻塞后的执行与验收"。
**记录日期：** 2026-11-19 建档（阻塞）；2026-11-23 更新为最终状态（America/Los_Angeles）。
**版本：** 阻塞期间为 `53415cd`，没有修改正式源码；解除阻塞后的唯一代码改动是 TASK-005.1（`baa5074`）。
**执行者：** Codex 执行 Phase 0–1C、Phase 1D 的脚本准备、TASK-005.1 修复和只读验收；Owner 手动运行所有真实请求和 UI 验收；Claude Code 负责独立审查与诊断，并整理本记录。

> 下面"阻塞结论"到"Owner 自查问题"是 2026-11-19 的原始记录，保留不改；其中部分判断在后续执行中被修正，修正内容见文末。
**证据目录：** `output/task005-real/`（已被 `.gitignore:228` 排除，不进入 Git）。

## 阻塞结论

**根因（高度可能，尚未得到 S2 确认）：旧 API Key 因约 60 天未使用，已被 Semantic Scholar 移除。**

- Key 发放日期是 2026-08-22；第一次真实采集是 2026-11-17，相隔 86 天，期间只运行过离线测试。
- S2 的 Key 申请表写明：Key 不活跃约 60 天以上可能被移除；如需恢复不活跃的 Key，需要重新提交申请（2026-11-19 查看申请页确认）。
- 被移除的 Key 不会返回 403，而是和匿名请求一样进入共享额度，返回 429。这与观测结果完全一致。

**已经排除的原因：**

| 假设 | 排除依据 |
|---|---|
| 请求频率过快 | 空闲约 55 小时后的第一个请求仍然返回 429（Phase 1B） |
| 代码没有发送 Key | mock 验证 `x-api-key` 请求头设置正确（`api.py:114-115`） |
| 环境变量错误或含空白 | 进程值与注册表 User 值完全一致，没有首尾空白；末尾字符与 S2 邮件一致（Owner 核对） |
| 代理出口 IP 被限流 | 确认已断开代理（`ProxyEnable=0`、`Meta` TUN 网卡未启用）后直连，仍然返回 429（Phase 1D 第 3 次运行） |
| 只有关系端点被限 | 轻量的元数据端点同样返回 429（Phase 1C/1D） |

## 各阶段真实请求记录

累计真实 HTTP 尝试 **10 次**，全部在授权范围内；没有自动增加预算、刷新缓存或重新采集。

| 阶段 | 时间（UTC） | 请求 | 结果 | 主要证据 |
|---|---|---|---|---|
| Phase 1 首次采集 | 2026-11-17 07:52 | 4 / 30：GelSight 元数据 1 次 + Touch and Go 元数据 3 次（含重试） | GelSight 200；其余 429。run 1 = `incomplete` | `acceptance-phase1.md`、`collect-first.json`、`sample-first.json` |
| Phase 1B 最小采集 | 2026-11-19 14:50 | 1 / 6：GelSight references `offset=0, limit=5`（元数据命中缓存） | 429，没有 Retry-After。run 2 = `incomplete` | `acceptance-phase1b.md`、`requests-phase1b.jsonl` |
| Phase 1C 定性诊断 | 2026-11-19 15:23 | 2 次：A 带 Key，B 匿名 | 都是 429 `TooManyRequestsException`，正文相同 | `acceptance-phase1c.md`、`phase1c-diagnosis.json` |
| Phase 1D 直连验证 | 2026-11-19 15:37 / 15:41 / 15:43 | 3 次，每次 1 个带 Key 请求 | 都是 429。只有第 3 次确认是直连 | `phase1d-direct-run1.json`、`-run2.json`、`phase1d-direct.json` |

**Phase 1D 三次运行的可信度：**

- run1：运行时的代理状态不明。
- run2：Owner 在运行前重新连接了代理，很可能走了代理。
- run3（`phase1d-direct.json`）：命令内置了"代理未断开就拒绝运行"的检查，**是唯一有效的直连证据**。

**可供 S2 排查的 RequestId（不敏感）：**

- `53ad67bd-1d95-4cc3-b3fe-957f034bba49`：直连，带 Key，2026-11-19T15:43:58Z
- `83b5a030-ed4d-4293-aa29-b5329ea9f9f5`：带 Key，2026-11-19T15:23:48Z
- `b6f39a0d-a639-4b2c-9291-0223c3e6cc10`：匿名对照，2026-11-19T15:23:55Z

## 当前数据状态

- `task005-real.db` 有 1 篇论文（GelSight，元数据来自 Phase 1，2026-11-17 抓取，7 天 TTL 内有效）、0 条边。run 1 和 run 2 都是 `incomplete`。
- 真实验收清单的所有项目**均未覆盖**：分页推进、重复记录、边方向、预算截断、缓存复测、标题搜索、UI 打开真实样本、导出。不能记为通过。
- Phase 0 的参数与命令方案仍然有效；Phase 1B/1C/1D 的临时脚本只放在证据目录里，不属于交付代码。

## 已采取的行动

1. 2026-11-19，Owner 回复了原 Key 邮件说明情况。收到的是自动回复，人工支持需要通过 `https://www.semanticscholar.org/about/contact` 提交。
2. 2026-11-19，Owner 用学校邮箱重新提交了 Key 申请（Private 用途，端点为 paper / references / citations / search）。提交成功：页面提示已收到申请，但 S2 正在处理积压的申请。S2 不会另外发确认邮件，唯一会收到的是标题为 "Your Semantic Scholar API Key" 的批准邮件。8 月那次申请约 20 分钟就自动批准了，这次因为有积压，审批时间无法预估。不要重复提交；如果约两周后（12 月初）仍未收到，就通过联系表单询问进度。

## 解除阻塞的步骤

1. 收到新 Key 后，更新 User 作用域的环境变量 `SEMANTIC_SCHOLAR_API_KEY`，然后**重启终端和 Codex**，否则进程里还是旧值。
2. 把 `phase1d-direct.json` 改名为 `phase1d-direct-run3.json`，再运行一次 `phase1d-direct.py --live`。**必须返回 200** 才能继续。
3. 使用 Phase 0 的参数和新的 run 重做 Phase 1：`citation_budget=30`、`reference_cap=20`、`page_size=5`、`request_budget=30`、`cache_ttl_hours=168`。之后按原计划进行缓存复测、标题搜索、UI 和导出验收。每一阶段都需要 Owner 单独授权。
4. 如果 2–3 周内没有收到新 Key，或者新 Key 仍然持续返回 429，就通过上面的联系表单，附上 RequestId 联系 S2。

## 风险与经验

- **Key 不活跃会被移除**：拿到新 Key 后，至少每 1–2 个月用它发一次真实请求；课堂演示前也要先用单请求确认 Key 有效。建议把这一条写进 README 的环境变量说明。
- **被移除的 Key 返回 429 而不是 403**：持续 429 时，先问"Key 上次什么时候用过"，不要先去调整请求间隔。
- **本机有代理**：本机运行 mihomo（Clash）并开启 TUN 网卡模式（`127.0.0.1:10090`），`requests` 在 Windows 上会自动读取系统代理。这次已经排除了代理的影响，但以后诊断网络问题时，要先确认出口。
- **诊断时要记录 429 的正文和非认证响应头**（`x-amzn-ErrorType`、`x-amzn-RequestId`）。Phase 1/1B 没有记录这些，导致多花了两个阶段才定性。

## Owner 自查问题

1. 为什么在 Key 失效、但代码完全正确的情况下，最终表现是 429 而不是 401 或 403？这说明"错误码"和"根因"之间是什么关系？
2. Phase 1D 的三次运行里，为什么只有第 3 次能当作证据？一个可靠的对照实验需要控制住哪些变量？

## 解除阻塞后的执行与验收（2026-11-19 至 2026-11-23）

新 Key 于 2026-11-19 批准（S2 当时提示有积压）。真实请求都由 Owner 在断开代理后手动运行，命令内置检查：代理没有断开或结果文件已存在时，拒绝运行。

| 步骤 | 时间（UTC） | 结果 | 证据 |
|---|---|---|---|
| 单请求测试 | 11-20 00:20 / 00:22 | 开代理 429；断开后 200，返回 GelSight 的正确 paperId 和标题 | `phase1d-direct-run4.json`、`phase1d-direct.json` |
| Phase 1R，3 篇种子（run 3） | 11-20 00:26 | `incomplete`，26/30 次尝试。3 篇种子都解析成功，取得 50 条边；Touch and Go 的 citations 被 429 拦下，Robot Synesthesia 的关系被跳过 | `collect-phase1r.json`、`acceptance-phase1r.md` |
| TASK-005.1 修复 | — | 请求间隔从 1.1 秒改为 2.0 秒；报告新增 `attempt_status_counts`（提交 `baa5074`） | Git 历史 |
| 补跑（run 4） | 11-20 00:51 | `incomplete`，6 次尝试 = 2×200 + 4×429。补齐了 Touch and Go 的 citations；Robot Synesthesia 的 references 连续 3 次 429 | `collect-phase1r2.json` |
| 两种子缓存运行（run 5） | 11-23 11:06 | ✅ **`bounded`，0 次 HTTP 请求**，4 个方向全部命中缓存；60 篇论文、60 条边 | `collect-phase1r3.json` |
| Phase 2 离线分析（run 5） | 11-23 | ✅ 60 条边方向全部正确；58 篇候选的依据全部与边一致；截断警告保留 | `acceptance-phase2.md`、`analysis-phase2.json`、`discovery-phase2.json` |
| UI 人工验收（run 5） | 11-23 | ✅ 顶部状态和数字、引用网络详情、候选分组、样本统计、数据质量表都正确 | Owner 截图 |
| 导出 | 11-23 | ✅ CSV（带 BOM）和 Markdown 都包含标题、年份、来源链接、关系依据、阅读理由、数据来源、采集范围、run_id、paperId；Markdown 保留了截断警告 | `papertrail-run-5.csv`、`papertrail-run-5.md` |
| 标题搜索（UI） | 11-23 | ✅ 输入 "Robot Synesthesia: In-Hand Manipulation with Visuotactile Sensing" 后返回目标论文；输入框保留原样的 "In-Hand"；"核对来源"打开 DOI 对应的 IEEE Xplore 页面，标题和作者一致 | Owner 截图 |

**验收清单最终状态：**

| 项目 | 状态 |
|---|---|
| 三篇种子元数据、DOI 和 arXiv 解析、DOI 路径编码 | ✅（run 3） |
| 标题检索与连字符处理 | ✅（UI 只显示标题和来源链接，不显示 paperId，所以只能按标题、DOI 和作者核对） |
| References 和 Citations 分页、offset 推进、无重复 | ✅（5 个方向；Robot Synesthesia 的两个方向未覆盖） |
| 请求预算严格生效 | ✅（每次 run 都没有超过上限） |
| 截断样本进入分析 | ✅（run 5） |
| 候选论文有可解释的种子关系 | ✅（CoRA-VTLA、TacBridge 同时引用两篇种子，在 UI 分组视图中排在"引用种子"组的第 1 名） |
| 第二次运行利用缓存 | ✅（run 4 和 run 5） |
| UI 打开真实样本、CSV 和 Markdown 导出 | ✅ |
| Robot Synesthesia 的关系数据 | ❌ 未取得（S2 间歇性 429） |

**对 2026-11-19 原始判断的修正：**

- **"代理出口 IP 被限流"**：旧 Key 时期直连也是 429，所以当时排除了代理。新 Key 时期，开代理 1 次 429、直连 1 次 200，但这只是各 1 次的对比；之后直连也频繁 429。**代理是否有影响，至今没有定论**。保守起见，真实请求仍在断开代理后运行。
- **"1.1 秒间隔太紧"**：改成 2.0 秒后，429 的比例没有下降（run 4 中 6 次尝试有 4 次 429，空闲 23 分钟后的第一个请求也是 429）。**间隔不是主要原因**。TASK-005.1 的价值在于 `attempt_status_counts` 让这一点可以被验证。
- **目前能确定的**：旧 Key 失效是阻塞的根因；换了新 Key 后，S2 仍然间歇性限流，从客户端这边查不出原因。已经成功的页会进入缓存，之后可以复用。

**数据特点与非阻塞问题：**

- citations 方向是按从新到旧取的，截断后的样本偏向最新论文；其中 2 篇的年份是 2027（期刊 online-first），属于数据源特点。
- CLI `discover.py` 默认按 `referenced_by_seed_count` 排序，在真实数据上有 40 篇并列，同时引用两篇种子的论文反而排到第 51 和 53 名。UI 的分组视图没有这个问题。演示和报告时请用 UI，或者在 CLI 里改用 `cites_seed_count` 排序。
- 样本统计的年份柱状图在 Owner 第一次截图里没有显示论文数只有 1–2 篇的年份。原因是图表被意外缩放：Streamlit 图表可以用滚轮缩放，双击图表恢复后，所有柱子都正常显示（Owner 2026-11-23 确认）。不是 bug。
- Markdown 导出把字段值放在行内代码里，是为了防止 Markdown 注入，但渲染出来不太好看，属于外观问题。

**后续（不阻塞 MVP）：**

1. 补齐 Robot Synesthesia：在 S2 不限流的时段，断开代理，用 3 篇种子、相同参数运行一次。已经完成的方向会命中缓存，只需要约 6 个请求。注意 7 天的缓存有效期。
2. 课堂演示用 run 5 这个现成的真实样本，**不要当场联网采集**。
3. 新 Key 至少每 1–2 个月用一次，避免再被移除。
