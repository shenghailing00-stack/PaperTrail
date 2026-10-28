# Agent instructions — PaperTrail (for Codex and other code agents)

Read `docs/PROJECT_BRIEF.md` first. It is the single source of truth for scope, milestones, acceptance tests, research limitations and course constraints.

## Roles and working agreement

- Human owner: product decisions, scope approval, real-world test, code explanation and merge/push authorization.
- **Codex: primary implementer**, unless owner explicitly assigns a different role.
- **Claude Code (CC): default independent reviewer and design critic**; it must not edit implementation files during review unless the owner explicitly authorizes a separate fix task.
- The owner may reverse roles for a task, but there must be **exactly one primary editor per change**.
- Never let two agents modify the same working tree simultaneously. For parallel independent changes, use separate Git branches/worktrees and human-reviewed PRs. Do not push, publish, merge or delete files without the owner's direction.

## Implementation expectations

1. Work on one numbered task at a time; present a concise plan and acceptance tests before a large code change.
2. Keep the smallest maintainable solution. Prefer built-in Python libraries before adding dependencies; avoid unrelated scaffolding.
3. Clearly separate API calls, persistence, analysis and UI. Do not place network calls in analysis functions or leak secrets into logs/commits.
4. HTTP: explicit timeouts; bounded retries and backoff for transient failures/429; pagination; safe failure with understandable messages; no indefinite loops or heavy uncontrolled API usage.
5. Data: stable IDs, deduplication, nullable fields, `citing -> cited` edge orientation, fetched timestamp, clear sample budget and missing/truncated flags.
6. Testing: mock HTTP in CI/offline tests; fixtures must not depend on a live API. Test edge orientation, deduplication, pagination, no-key path, bad IDs, rate limits and empty results as relevant.
7. Reproducibility: document Python version, install/run/test commands, environment variables; keep `*.db`, `.env`, credentials, caches and large raw datasets out of Git unless explicitly agreed.
8. Every output claim must be supported by actual implementation/tests; do not claim a live API test succeeded if it was not executed.
9. No premature PageRank/classic-baseline classification; sampled graph measurements must be labeled and qualified.
10. Explain important choices to a CS undergraduate learning the code; favor readable functions and small modules over clever abstractions.

## Required handoff at the end of each task

- What changed: paths and behavior.
- Evidence: commands run and test results, including failures/unverified items.
- Design decisions: 2–4 important trade-offs.
- Owner learning: 2–3 code locations to read, and one simple manual check/question.
- Next action, risks or data/API limitations.

Do not build future features during a narrowly scoped proof-of-concept.
