# TASK-005 — 真实 Semantic Scholar 数据端到端验证

**状态：** 🔴 **阻塞**，等待新的 Semantic Scholar API Key。Phase 0 预检完成；Phase 1 到 1D 的真实请求都返回 HTTP 429，没有取得任何引用关系数据。
**记录日期：** 2026-11-19（America/Los_Angeles）。
**版本：** `main` / `53415cd feat: add Streamlit research exploration UI`。本任务没有修改正式源码。
**执行者：** Codex 执行 Phase 0–1C 和 Phase 1D 的脚本准备；Owner 手动运行 Phase 1D；Claude Code 负责独立审查与诊断，并整理本记录。
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
2. 2026-11-19，Owner 用学校邮箱重新提交了 Key 申请（Private 用途，端点为 paper / references / citations / search）。

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
