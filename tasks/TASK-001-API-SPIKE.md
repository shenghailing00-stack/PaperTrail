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

- [ ] Setup instructions work on Windows 11 in a fresh Python environment.
- [ ] Metadata request responds with paperId/title/year/author/url when available, and documents missing fields.
- [ ] References have `citedPaper`; citations have `citingPaper`; printed examples explain `citing -> cited` direction.
- [ ] First 5 reference and first 5 citation items are saved to local JSON (or exact empty/error status is reported).
- [ ] Output shows `contexts`/`intents`/`isInfluential` for each reference/citation item, or states that they are empty/unavailable.
- [ ] Coverage check run live on each owner-supplied demo seed: references returned vs. unresolved (`paperId: null`) vs. empty list (possible publisher elision), plus how many items have non-empty `contexts`. Results recorded in the handoff; seeds with poor coverage are flagged as unsuitable for the demo.
- [ ] `pytest` offline suite passes; live status is stated separately, honestly.
- [ ] API key is neither committed nor printed.
- [ ] Human owner can point to the request function and explain JSON parsing and exception handling.

## Demo seeds (owner-selected 2026-10-28)

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
