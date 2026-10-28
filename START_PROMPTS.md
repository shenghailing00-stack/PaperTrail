# First prompts to send to each coding assistant

## Codex — start TASK-001 (primary developer)

You are the primary implementer for the PaperTrail project. Please first read `docs/PROJECT_BRIEF.md`, `AGENTS.md` and `tasks/TASK-001-API-SPIKE.md` in this repository. We are at TASK-001 only. Before writing code, give a concise implementation plan, list intended files, summarize Semantic Scholar endpoints and request fields based on the current official docs, and tell me how you will test without repeatedly hitting the public API. Wait for my approval before implementing. During the implementation, do not add features from later milestones and do not push to GitHub. Afterward, explain request/response JSON, Python exception handling, and mock tests to me at an undergraduate level.

## Claude Code — review Codex's plan (read-only reviewer)

You are the independent reviewer for PaperTrail. Read `docs/PROJECT_BRIEF.md`, `AGENTS.md`, `CLAUDE.md`, and `tasks/TASK-001-API-SPIKE.md`. Your job in this stage is to REVIEW Codex's TASK-001 plan, not write code. Look for incorrect API assumptions, missed error cases, unnecessary complexity, security issues and scope creep. Give blocking findings first, followed by minimal actionable suggestions, and 2–3 specific things the project owner should understand. Do not modify files or create commits unless I explicitly assign a fix task.

## Claude Code — review after Codex implements (read-only reviewer)

Please read `docs/PROJECT_BRIEF.md`, `AGENTS.md`, `CLAUDE.md` and the TASK-001 acceptance criteria. Review the actual changed files and tests. Verify, where possible, API response parsing, `citing -> cited` edge semantics, timeouts/retries, rate-limit behavior, secret handling, fixture quality and reproducibility. Rank findings by severity and include file/line references. Distinguish tests you ran from claims you could not verify. Do not change code. End with 3 short learning questions for the owner.

## Project owner — validation ritual

After each task: (a) run the documented command yourself; (b) open the exported JSON/figure or UI; (c) read 2–3 key functions and explain them; (d) approve/reject based on acceptance criteria; (e) only then commit/merge. If either agent cannot prove something, record it as pending rather than declaring success.
