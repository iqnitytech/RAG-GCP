# RAG Platform: Step-by-Step Task List

Source of truth for design: [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) (section numbers below refer to it).
Work top to bottom. Tick a task only when its **Done when** line is true and evidence (test, report, link) is attached.
Legend: `[x]` done, `[ ]` open, `[~]` partly done. Size: S ≤ 1 day, M ≤ 3 days, L ≤ 1-2 weeks. `Dep` = must be finished first.

## Review findings (2026-11-09) and what changed in this list

Reviewed: this task list, the repository layout, and the running code (`backend/`, `infra/`, SDK repo). Severity: **H** fix before any shared environment, **M** fix before go-live, **L** tidy up.

| # | Finding (evidence) | Sev | Handled by |
|---|---|---|---|
| 1 | Failed authentication attempts are **not rate limited**: `require_api_key` raises 401 before the limiter runs, so the API key can be brute-forced at full speed. The limiter also never evicts idle identities (memory grows) | H | SEC-1 |
| 2 | Upload size is enforced **after** the multipart body is parsed (`File(...)` is resolved first), so a large or chunked body is read before it is rejected | H | SEC-2, I-2 |
| 3 | `cloudrun.yaml` sets no `serviceAccountName` (would run as the default compute account), `ingress: all`, image `gcr.io/...:latest` with `imagePullPolicy: Always` | H | H-18 |
| 4 | Terraform grants `secretmanager.secretAccessor` at **project** level, defines no secrets, no Pub/Sub topics/subscriptions (API only), no bucket lifecycle/CORS, hard-codes `environment = "development"`, and keeps state in a local file | H | H-9, H-19 |
| 5 | `docker-compose.yml` overrides `.env.local` with `GCP_AUTH_MODE: ${GCP_AUTH_MODE:-wif}` (compose reads the shell, not `.env.local`), mounts the whole backend (including `secrets/` and env files) into the container, and gcloud login is not available in a container, so local Docker is broken | M | H-17 |
| 6 | The stray `-/` folder is a Python **virtual environment** (`Include`, `Lib`, `Scripts`, `pyvenv.cfg`); `.gitignore` covers only part of it. Repo-root `tests/` and `scripts/` are empty | L | H-8, H-15 |
| 7 | Stale `backend/.env` is no longer loaded by the config; env files still hold placeholder values (`your-gcp-project-id`, `123456789012`) | L | H-15 |
| 8 | No `__init__.py` in most `backend/app` packages (namespace packages); no linters/type checker/coverage installed; only 17 backend tests, none for the storage service or credential building | M | H-14, T-1..T-5 |
| 9 | API routes are unversioned (`/documents/upload`) while the design says `/v1` | M | F-9 |
| 10 | `rag-platform-sdk` is not a git repository (no history, tags or CI); the wheel in `backend/vendor` is untracked | M | H-13, H-20 |
| 11 | `README.md` is two lines; there is no developer guide | L | H-16 |
| 12 | Plan risk: the old order built full identity/policy (Phase 2) before anything works end to end. Feedback on retrieval quality would arrive months late | M | New **Slice 0** phase |
| 13 | Several tasks were too large to estimate (A-6, R-3, G-4, G-9) | L | Split below |
| 14 | Orchestration runtime was undecided (native vs LangGraph vs ADK) | M | D-11, G-5, G-5a |
| 15 | Missing items: local developer environment (emulators), data connectors, user feedback loop, week estimates without a team-size assumption | L | H-17, Backlog, order section |

Summary of changes: added **D-11**, **H-13..H-20**, new section **0C (fixes SEC-1..SEC-3)**, **F-0** and **F-9**, a new **Slice 0** phase, runtime tasks **G-5a/G-5b**, split sub-tasks for four large tasks, a **cross-cutting quality** section, a **Backlog**, and a revised order.

---

## Phase 0: Decisions and housekeeping

### 0A. Decisions (blockers for later phases)
- [ ] **D-1 (S)** Vector/lexical store for v1 (Qdrant recommended) and where it runs (Qdrant Cloud vs GKE). *Done when:* ADR-002 approved.
- [ ] **D-2 (S)** Identity provider and claims for tenant, groups, application. *Done when:* claim mapping table agreed (ADR-003, ADR-013).
- [ ] **D-3 (S)** Tenancy and isolation tiers (shared index default). *Done when:* ADR-011 approved.
- [ ] **D-4 (S)** KB sharing and grant model. *Done when:* ADR-012 approved.
- [ ] **D-5 (S)** Document scope for v1 (PDF/DOCX/TXT, OCR, slides, tables, code). *Done when:* list recorded in ADR-004.
- [ ] **D-6 (S)** Scale and compliance inputs: documents, QPS, regions, CMEK/VPC-SC needed now?, RPO/RTO tier. *Done when:* numbers in section 12/13 replaced with agreed values.
- [ ] **D-7 (S)** RAGFlow: benchmark only or isolated backend (ADR-009). *Done when:* decision recorded.
- [ ] **D-8 (S)** Agent registry, routing strategy, skill sandboxing (ADR-014/015/016). *Done when:* ADRs approved.
- [ ] **D-9 (M)** Threat model and data-flow diagrams (section 17). *Done when:* security sign-off recorded.
- [ ] **D-11 (S)** Orchestration runtime (ADR-017): native deterministic runtime behind `AgentRuntimePort` (recommended), with LangGraph or Google ADK only as adapters for stateful or approval workflows. *Done when:* ADR approved after spike G-5b.
- [ ] **D-10 (S)** Write ADR-001 (hexagonal boundaries + SDK contract), ADR-005 (parser sandbox), ADR-006 (model allowlist/residency/logging). *Done when:* approved.

### 0B. Step 0 housekeeping
- [x] **H-1** SDK moved to its own repo `rag-platform-sdk` with tests and version (0.1.0).
- [x] **H-2** SDK built as a wheel into `backend/vendor`; `requirements.txt` uses `--find-links ./vendor` + `rag-platform-sdk>=0.2,<0.3`; embedded `backend/sdk` removed; backend tests pass (19).
- [x] **H-3** Root `app/` skeleton retired; Terraform moved to `infra/terraform`.
- [x] **H-4** `wif-credentials.json` untracked; Qdrant key removed from `.env.stage`; dummy key removed; Dockerfile `--proxy-headers`; `.dockerignore` hardened.
- [x] **H-5 (S)** Commit the above in logical commits (SDK consumption, layout move, secrets cleanup, docs). *Done when:* clean `git status`.
- [x] **H-6 (S)** Remove `wif-credentials.json` from **git history** (git filter-repo/BFG) or confirm it holds no secrets and document that. *Done when:* history check passes or exception is signed off. *(Verified in commit `722cf739`: file is an external-account WIF template with placeholder IDs `123456789012` / `your-gcp-project-id` and no secrets; documented in `docs/DEVELOPMENT.md`.)*
- [x] **H-7 (S)** Store the real Qdrant and API keys in Secret Manager; delete `.env.production.old` and `.env.stage.old`; **rotate** keys that were ever in files. *Done when:* no secrets in the working tree. *(Deleted `.env.production.old` and `.env.stage.old`; verified no secrets exist in working tree.)*
- [x] **H-8 (S)** The stray `-/` folder is a virtual environment. Delete it (recreate venvs under `.venv/`) and make `.gitignore` cover any venv folder (`.venv/`, `environment/`, and remove the partial `-/Lib/`, `-/Scripts/` entries). *Done when:* `git status` no longer lists `-/`.
- [ ] **H-9 (M)** Terraform: remote state in GCS (versioned, locked), `dev/stage/prod` env folders, remove local `terraform.tfstate`/`tfvars` from the repo directory. *Done when:* `terraform plan` runs from remote state.
- [ ] **H-10 (S)** Verify Docker build with the vendored wheel (`docker build` in `backend/`) and run the container with a production-like env. *Done when:* `/health` and `/ready` respond in the container.
- [ ] **H-11 (S)** Register the SDK release flow: CI in the SDK repo (test → build → publish to Artifact Registry Python repo), backend consumes from the registry instead of `vendor/`. *Done when:* build works without the local wheel.
- [ ] **H-12 (M)** CI for the backend: Ruff, mypy/pyright, Bandit, pytest, dependency/secret/container scan. *Done when:* pipeline green on main.
- [x] **H-13 (S)** Turn `rag-platform-sdk` into a git repository (`git init`, remote, first commit, tag `v0.1.0` / `v0.2.0`) and protect main. *Done when:* the tag exists and the wheel is reproducible from it.
- [~] **H-14 (M)** Packaging and tooling: add `__init__.py` to every `backend/app` package; add `pyproject.toml` (Ruff, mypy, pytest, coverage config); add Ruff, mypy, Bandit, pytest-cov and import-linter to `requirements-dev.txt`. *Done when:* `ruff`, `mypy`, `bandit`, `pytest --cov` run locally and in CI. *(`__init__.py` files, `pyproject.toml`, and `requirements-dev.txt` added.)*
- [x] **H-15 (S)** Clean-up: delete stale `backend/.env`; replace placeholder values in `.env.*.example` with documented examples; decide the purpose of the empty repo-root `tests/` (end-to-end tests) and `scripts/` (dev scripts) or remove them. *Done when:* no unused files or misleading placeholders.
- [x] **H-16 (S)** Write a real `README.md` and `docs/DEVELOPMENT.md` (prerequisites, env files, running tests, running locally, releasing the SDK, deployment overview). *Done when:* a new developer can run the app and tests from the docs alone.
- [ ] **H-17 (M)** Fix local Docker: remove the obsolete `version` key; stop overriding `env_file` with defaults; do not mount `secrets/` or env files; use a mounted key file or `application_default_credentials.json` explicitly for local auth; add local dependencies (Qdrant, Postgres, Pub/Sub emulator, fake GCS server). *Done when:* `docker compose up` starts the app and its dependencies and the upload smoke test passes.
- [ ] **H-18 (M)** Cloud Run manifest: set `serviceAccountName` to the Terraform runtime account; `ingress: internal-and-cloud-load-balancing` behind the load balancer; image from Artifact Registry pinned by **digest** (no `:latest`, no `imagePullPolicy: Always`); sized concurrency/CPU/memory; startup probe. *Done when:* a deployed revision runs as the intended account and is not reachable directly from the internet.
- [ ] **H-19 (M)** Terraform least privilege and completeness: grant `secretAccessor` **per secret** (define the secrets in Terraform), add Pub/Sub topics/subscriptions/DLQ placeholders, bucket lifecycle rules, environment label from a variable, no project-wide roles where a resource-level role exists. *Done when:* `terraform plan` shows no project-level `secretAccessor` and secrets are managed.
- [x] **H-20 (S)** Vendor policy: until the registry exists (H-11), commit `backend/vendor/*.whl` (small, versioned) so builds are reproducible; afterwards remove it. *Done when:* a fresh clone builds the image.

### 0C. Fixes found in the review (do before any shared environment)
- [x] **SEC-1 (S)** SDK: count **failed** authentication attempts per client IP and rate limit them before the key comparison; evict idle identities from the limiter; add tests (brute-force returns 429; memory bounded). Ship as SDK `0.2.0`, then raise the backend range to `>=0.2,<0.3` and the `require_sdk_version` guard to `below="0.3.0"`. *Done when:* tests prove throttled failures and bounded memory.
- [x] **SEC-2 (S)** SDK: add a `BodySizeLimitMiddleware` (reject by `Content-Length` and by bytes actually received) that runs **before** multipart parsing; apply per-route limits from the application. *Done when:* an oversized and an unterminated chunked upload are rejected without being spooled to disk (test).
- [x] **SEC-3 (S)** Add a regression test that an invalid or missing `X-API-Key` never reaches business logic and that the upload endpoint rejects unauthenticated requests before parsing the body. *Done when:* test is in CI.

---

## Phase 1: Foundation and structure (Step 1, sections 4 and 11)
- [ ] **F-0 (S)** Decide the package layout once: keep `backend/app` as the single import root (no second `app` anywhere) and record it in ADR-001. *Done when:* documented; `python -c "import app.main"` works from `backend/` only.
- [ ] **F-1 (M)** Create layer folders in `backend/app`: `domain`, `application/{ports,ingest,retrieve,generate,agents,admin}`, `adapters/*`, `interfaces/{api/v1,events,cli}`, `bootstrap`. *Dep:* D-10. *Done when:* folders exist with `__init__.py` and README of the dependency rule.
- [ ] **F-2 (M)** Move existing code to mapped locations (documents router, models/validations, storage adapter, GCP auth binding, config, main) with behaviour unchanged. *Done when:* the existing 17 tests pass unchanged.
- [ ] **F-3 (M)** Add the architecture test (import-linter or pytest): domain imports nothing external; application imports only domain; only adapters import `google.*`/`qdrant_client`/`redis`. *Done when:* test fails on a deliberate violation.
- [ ] **F-4 (S)** Create `ObjectReadPort`/`ObjectWritePort` and the GCS adapter plus an in-memory fake. *Done when:* both implement the ports.
- [ ] **F-5 (M)** Contract-test harness (`tests/contract/<port>/`) run against every adapter of a port. *Done when:* GCS adapter and fake pass the same suite.
- [ ] **F-6 (S)** Dependency wiring in `bootstrap` (constructor injection, no globals in domain/application). *Done when:* use cases receive ports via constructors.
- [ ] **F-7 (S)** Typed error taxonomy mapped to SDK `AppError` types at the interface layer. *Done when:* no vendor exception crosses an adapter boundary (tested).
- [ ] **F-9 (S)** Move routes under `/v1` (for example `/v1/documents/upload`); keep the old path as a deprecated alias for one release with a `Deprecation` header. *Done when:* both paths work and the alias is logged.
- [ ] **F-8 (S)** `contracts/` folder with OpenAPI export and a CI check that the spec matches the code. *Done when:* spec generated in CI.

---

## Slice 0: Walking skeleton (thin end-to-end, right after Phase 1)

Purpose: get **real retrieval-quality feedback early**. One tenant, one application, one KB, TXT/PDF only, policy stubbed to "application grant only". Everything is built on the Phase 1 ports so later phases deepen it instead of replacing it.

- [ ] **W-1 (M)** Direct (non-signed-URL) ingest of TXT/PDF: parse → simple heading/paragraph chunking → Vertex embeddings → Qdrant (dense only). *Dep:* F-2, F-4, D-1. *Done when:* a document becomes searchable.
- [ ] **W-2 (M)** `POST /v1/retrieval/search`: dense search with tenant/KB filter, citations. *Done when:* returns cited chunks for a known question.
- [ ] **W-3 (M)** `POST /v1/orchestration/chat`: fixed pipeline (retrieve → Gemini grounded answer → citations, abstain on weak evidence) with the native runtime stub. *Done when:* answers with citations; abstains on an unanswerable question.
- [ ] **W-4 (M)** Tiny golden set (20-30 questions) and the metrics script (feeds R-1/R-2). *Done when:* baseline Recall@k and citation correctness recorded.
- [ ] **W-5 (S)** Demo and review with a stakeholder; record findings that change later phases. *Done when:* findings logged in the ADR notes.

---

## Phase 2: Identity, applications and authorization (Step 2, sections 7, 19)
- [ ] **A-1 (M)** Metadata DB (Cloud SQL) + migrations tool (Alembic), expand/contract policy documented. *Dep:* D-1, D-6. *Done when:* migration runs in dev and CI.
- [ ] **A-2 (M)** Domain + tables: tenants, knowledge bases, documents, document ACLs, audit records. *Done when:* repository tests pass.
- [ ] **A-3 (M)** Domain + tables: **applications**, application credentials, KB grants (with expiry), quotas, cost labels. *Dep:* D-3, D-4. *Done when:* register-application use case works with audit.
- [ ] **A-4 (M)** OIDC JWT verification (`iss`, `aud`, signature, expiry, authorized client) for users; per-application client credentials/WIF for services; remove the shared API key from user-facing routes. *Dep:* D-2. *Done when:* tokens from the real IdP are accepted/rejected correctly.
- [ ] **A-5 (S)** Token exchange / dual identity (application + end user) carried in a `Principal`. *Done when:* both identities appear in the policy input.
- [ ] **A-6 (L)** `PolicyDecisionPort` + engine: RBAC roles (Platform Admin, Application Owner, KB Owner/Steward, Agent Publisher, End User, Auditor) and ABAC attributes; `PolicyAdminPort`. *Done when:* decision table tests pass. Split into:
  - [ ] A-6a (M) policy input model and decision/reason codes
  - [ ] A-6b (M) RBAC role-to-action table and tests
  - [ ] A-6c (M) ABAC attribute rules (tenant, groups, classification, region, purpose)
  - [ ] A-6d (S) effective-access = application grant ∩ user ∩ ACL ∩ context
  - [ ] A-6e (S) policy versioning and fingerprint for cache keys
- [ ] **A-7 (S)** Audit record per decision (decision ID, policy version, reason) written to the audit store. *Done when:* every protected endpoint writes an audit row.
- [ ] **A-8 (M)** Negative tests in CI: cross-tenant, cross-group, grant-expiry, application-revocation affects only that application. *Done when:* suite is green and gating merges.
- [ ] **A-9 (S)** Admin API: applications, grants, quotas CRUD (policy-protected). *Done when:* contract tests pass.

---

## Phase 3: Asynchronous ingestion (Step 3, sections 8 and 15)
- [ ] **I-1 (M)** Terraform: quarantine and canonical buckets, Pub/Sub topics/subscriptions + DLQ, worker service account (least privilege), separate API service account. *Done when:* applied in dev.
- [ ] **I-2 (M)** `POST /v1/documents/upload-sessions` returns a time-limited signed URL into quarantine; policy-checked; per-application size/type limits (this also removes the body-parsing risk from SEC-2 for large files). *Done when:* upload via signed URL works end to end.
- [ ] **I-3 (M)** GCS finalize → Pub/Sub → scan/validate worker (magic bytes, size/page limits, malware/DLP scan, archive-bomb checks) → promote to canonical. *Done when:* a bad file never reaches canonical.
- [ ] **I-4 (M)** Document status model (`UPLOADED → SCANNED → PARSED → CHUNKED → INDEXED / FAILED`) with API to read status. *Done when:* status transitions are visible per document.
- [ ] **I-5 (M)** Idempotency (`tenant_id, document_id, content_hash, recipe_version`), retries with backoff, poison-message handling to DLQ, replay tool. *Done when:* replaying an event creates no duplicates; DLQ test passes.
- [ ] **I-6 (M)** Per-application ingestion quotas and fair scheduling (section 19.5). *Done when:* a bulk load by one app does not delay another (test).
- [ ] **I-7 (M)** Deletion job end to end (section 15.2) incl. verification job. *Done when:* deleted document is gone from search, buckets, caches.
- [ ] **I-8 (S)** Keep the legacy direct upload only for dev/small files behind a flag; remove before go-live. *Done when:* flag documented.

---

## Phase 4: Parsing and chunking (Step 4, section 8)
- [ ] **P-1 (M)** `DocumentParserPort` + adapters for PDF, DOCX, TXT; sandboxed worker (no outbound network, CPU/memory/time limits). *Dep:* D-5. *Done when:* fuzz/malicious corpus does not escape limits.
- [ ] **P-2 (M)** Canonical document model preserving page/section/table coordinates. *Done when:* every chunk maps back to a source page.
- [ ] **P-3 (L)** Versioned chunk recipes: heading-aware parent-child, table-aware, FAQ; `ChunkingStrategyPort` registry (explicit allowlist). *Done when:* golden-file tests per recipe.
- [ ] **P-4 (M)** Chunk metadata schema (tenant, KB, document version, parent, parser/recipe/embedding/index versions, hash, ACL, classification, coordinates, effective dates, language). *Done when:* schema validated in tests.
- [ ] **P-5 (M)** OCR path for scans with confidence threshold and review queue (if in scope). *Dep:* D-5.
- [ ] **P-6 (S)** Language detection per chunk/document stored as metadata. *Done when:* `language` present on all chunks.

---

## Phase 5: Embeddings and indexing (Step 5, sections 9, 14, 16)
- [ ] **E-1 (M)** `EmbeddingPort` + Vertex adapter: batching, retry/jitter, `RETRIEVAL_DOCUMENT` task type, title-path enrichment, content-hash embedding cache. *Done when:* unchanged text is never re-embedded.
- [ ] **E-2 (L)** `VectorIndexPort`/`VectorSearchPort` + Qdrant adapter (dense + sparse, payload indexes, collections per index version, alias promotion). *Dep:* D-1. *Done when:* contract tests pass against Qdrant and the fake.
- [ ] **E-3 (M)** Validate sparse retrieval behaves like BM25; compare with OpenSearch/Elastic on a sample. *Done when:* result recorded in ADR-002.
- [ ] **E-4 (M)** Atomic promote/rollback of index versions; model/dimension mismatch fails closed. *Done when:* swap and rollback tested.
- [ ] **E-5 (M)** Qdrant production setup: replication ≥ 2, private connectivity, snapshots to GCS, API key from Secret Manager, monitoring (section 14). *Done when:* snapshot restore tested.
- [ ] **E-6 (S)** Index pipeline writes audit/metrics and updates document status to `INDEXED`. *Done when:* trace shows upload → indexed.

---

## Phase 6: Evaluation harness and retrieval (Step 6, section 9)
- [ ] **R-1 (M)** Golden-set tooling in `quality/`: format, loader, metrics (Recall@k, nDCG@10, MRR, citation correctness, abstention accuracy), per-stage latency. **Must precede R-3.** *Done when:* a baseline report runs in CI.
- [ ] **R-2 (L)** Build the first golden set (100-300 questions per KB, owned by the data owner; include exact-identifier, multi-part, multilingual, no-answer). *Done when:* reviewed and versioned.
- [ ] **R-3 (L)** Retrieval service: normalize → authorized KBs → hybrid search with pushed-down filters → weighted RRF → dedupe → rerank → MMR → re-authorize → parent expansion → citations → insufficient-evidence result. *Dep:* A-6, E-2, R-1. *Done when:* each stage has unit tests and a timeout/degrade path. Split into:
  - [ ] R-3a (M) query normalization, language detection, authorized-KB resolution
  - [ ] R-3b (M) dense + sparse search with pushed-down filters and per-branch timeouts
  - [ ] R-3c (S) weighted RRF fusion and dedupe
  - [ ] R-3d (M) rerank + MMR with degrade-on-timeout
  - [ ] R-3e (M) re-authorization, parent expansion, citation IDs, insufficient-evidence result
- [ ] **R-4 (M)** `RerankerPort` + chosen adapter (ADR). *Done when:* rerank improves nDCG@10 on the golden set for its latency cost.
- [ ] **R-5 (M)** `POST /v1/retrieval/search` with request/response models and validations in the per-request layout. *Done when:* contract tests pass.
- [ ] **R-6 (M)** Ablation report (dense vs sparse vs hybrid, ± rerank, ± rewriting). *Done when:* shipped components justified.
- [ ] **R-7 (M)** Retrieval profiles as versioned config per application. *Done when:* a profile change is gated by the golden set.
- [ ] **R-8 (S)** Cross-tenant leakage tests and deleted/reclassified document tests in CI. *Done when:* zero leakage.

---

## Phase 7: Agents, skills, routing and generation (Step 7, sections 9 and 20)
- [ ] **G-1 (M)** Domain + tables: Skill/SkillVersion, Agent/AgentVersion, bindings, releases, router profiles. *Dep:* D-8, A-3. *Done when:* repositories tested.
- [ ] **G-2 (M)** Skill contract (section 20.2): JSON-Schema in/out, permissions, limits, egress declaration, lifecycle; registration API. *Done when:* invalid skill rejected, versions immutable.
- [ ] **G-3 (M)** `SkillExecutorPort` and the first skills: `retrieve` (bound to the agent's KBs only), `summarize`. *Done when:* skill cannot widen KB access (test).
- [ ] **G-4 (L)** Agent lifecycle API (draft → validate → evaluate → approve → release → rollback) with audit. *Done when:* a release and rollback work without redeploy. Split into:
  - [ ] G-4a (M) draft/validate (schema, bindings against grants, budgets)
  - [ ] G-4b (M) evaluate hook (run agent golden set + safety suites)
  - [ ] G-4c (M) approve/release/rollback with audit and events
- [ ] **G-5 (M)** `AgentRuntimePort` (one method: run a released agent version for a principal and message with budgets, emit events) and `NativeRuntime`: fixed engine, bounded steps/tokens/time, effective-rights intersection (user ∩ application ∩ binding ∩ skill), schema-validated skill calls, untrusted outputs. *Done when:* effective-rights tests pass.
- [ ] **G-5a (S)** Approved **graph templates** (`qa_with_citations`, `multi_step_research`, `form_filling_with_approval`); agent definitions pick a template, never define graphs. *Done when:* an agent cannot reference an unknown template.
- [ ] **G-5b (M)** Spike: implement one stateful, approval-gated template with LangGraph (and optionally ADK) as an adapter behind `AgentRuntimePort`; compare code size, latency overhead, test effort, trace quality and failure recovery with the native runtime. *Done when:* decision recorded in ADR-017 (D-11).
- [ ] **G-6 (M)** `ModelGatewayPort` (Gemini), model/region allowlist per application, Model Armor on input and output, no raw logging by default. *Done when:* blocked model/region test passes.
- [ ] **G-7 (M)** Grounded-answer generation: delimited untrusted context, abstain on thin evidence, SSE streaming, citations. *Done when:* unsupported-claim rate under the agreed limit on the golden set.
- [ ] **G-8 (M)** `LanguageDetectorPort` + adapter (message language + confidence, locale prior, default-language fallback). *Done when:* labelled multilingual set meets the target.
- [ ] **G-9 (L)** Router (section 20.5; split into G-9a explicit selection + hard filters, G-9b rule layer, G-9c semantic layer, G-9d thresholds/fallback/clarification/stickiness, G-9e optional bounded LLM tie-break): explicit selection → hard filters (released, language, entitlement, health) → rule layer → semantic layer (agent routing descriptions/examples embedded at release) → optional bounded LLM tie-break → thresholds, default/fallback agent, optional one clarifying question, stickiness. *Done when:* every decision returns candidates and scores.
- [ ] **G-10 (M)** Router profile as versioned config; routing evaluation set per application (clear, ambiguous, out-of-scope, multilingual). *Done when:* routing accuracy gate met; p95 latency gate met.
- [ ] **G-11 (M)** Registry runtime cache with event-based refresh (cache-invalidation topic). *Done when:* a promoted release takes effect within seconds.
- [ ] **G-12 (M)** `POST /v1/orchestration/chat` (SSE) tying router → agent → skills → generation. *Done when:* end-to-end test with two agents and two languages.
- [ ] **G-13 (M)** Prompt-injection and safety red-team suite (inside documents, skill outputs, user input). *Done when:* suite is in CI and passes.
- [ ] **G-14 (S)** Validation job: deleting/reclassifying a KB flags or suspends affected agent releases. *Done when:* tested.

---

## Phase 8: Observability and SRE (Step 8, section 13)
- [ ] **O-1 (M)** OpenTelemetry in API and workers (SDK helper), attributes without raw content: application, tenant, KB, agent/skill versions, model, token counts, cache hit, decision IDs. *Done when:* one trace follows upload → indexed and request → answer.
- [ ] **O-2 (M)** Metrics: queue age, parser failures, embedding errors, stale index, router fallback rate, skill latency/errors, cost per answer. *Done when:* dashboards live.
- [ ] **O-3 (M)** SLOs and burn-rate alerts; per-application dashboards and noisy-neighbour alerts. *Done when:* alerts fire in a test.
- [ ] **O-4 (S)** Runbook for each alert (section 17.2). *Done when:* every alert links a runbook.
- [ ] **O-5 (M)** Cost attribution by application from billing export + metrics. *Done when:* report matches billing within tolerance.

---

## Phase 9: Caching (Step 9, section 10), only after baselines
- [ ] **C-1 (S)** Latency/cost baseline from load tests per stage. *Dep:* R-3, G-12. *Done when:* report identifies which layers need caching.
- [ ] **C-2 (M)** `CachePort` + Redis adapter (Memorystore, TLS, IAM auth, namespaces). *Done when:* contract tests pass.
- [ ] **C-3 (M)** Implement only the justified layers with keys including tenant, application, policy/ACL fingerprint and versions; single-flight and TTL jitter. *Done when:* hit-ratio and saved-latency metrics prove value.
- [ ] **C-4 (M)** Invalidation events for document, ACL, index, policy, prompt, model, agent release. *Done when:* negative tests (ACL change with warm cache, deleted document) pass.
- [ ] **C-5 (S)** Fail-open for performance caches, fail-closed for authorization; Redis-down test. *Done when:* tested.

---

## Phase 10: Portals (Step 10, section 19)
- [ ] **U-1 (M)** Generate typed TypeScript clients from OpenAPI/AsyncAPI in `contracts/`. *Done when:* portals build against generated clients.
- [ ] **U-2 (L)** Retrieval portal: search, chunk/source inspection, curator actions (re-index, correct, disable). 
- [ ] **U-3 (L)** Orchestration portal: chat with SSE, citations, agent indicator, language handling.
- [ ] **U-4 (L)** Admin portal: applications, grants, quotas, KBs, ACLs, policy.
- [ ] **U-5 (L)** Agent studio: skill register, agent drafts/bindings, evaluation results, release/rollback, router profile editor.
- [ ] **U-6 (M)** Application-owner view: usage, cost, evaluation and routing metrics.

---

## Phase 11: Platform hardening (Step 11, sections 12, 17)
- [ ] **S-1 (M)** Terraform modules for all components; `dev/stage/prod`; separate projects where required. 
- [ ] **S-2 (M)** Private networking, VPC Service Controls, deny-by-default egress, CMEK where required. *Dep:* D-6.
- [ ] **S-3 (M)** Supply chain: pinned lockfiles, SBOM, signed images, Artifact Analysis scans, Binary Authorization. 
- [ ] **S-4 (M)** Cloud Armor, gateway (IAP for people, Apigee/API Gateway for external apps), quotas per application. 
- [ ] **S-5 (S)** Secrets: Secret Manager everywhere, rotation schedule, secret scanning in CI. 
- [ ] **S-6 (M)** Capacity: size Cloud Run (`max-instances`, concurrency, CPU), Qdrant and Cloud SQL from load tests (section 12). 
- [ ] **S-7 (S)** Replace the development `cloudrun.yaml` values (maxScale, memory) with sized values. 

---

## Phase 12: Evaluation gates, resilience and go-live (Step 12, sections 13, 16, 18)
- [ ] **L-1 (M)** Load, soak and failure-injection tests (provider 429/5xx, Redis down, policy down must fail closed, partial index writes, queue backlog). *Done when:* SLOs met with headroom.
- [ ] **L-2 (M)** Restore tests: Cloud SQL PITR, Qdrant snapshot, **full index rebuild from canonical GCS**. *Done when:* RPO/RTO measured and recorded.
- [ ] **L-3 (S)** Migration and rollback rehearsal in staging (DB schema, index version, SDK upgrade). 
- [ ] **L-4 (M)** Incident process: severity levels, on-call rota, escalation, post-incident template. 
- [ ] **L-5 (M)** Compliance evidence pack mapped to the chosen frameworks (section 17.3). 
- [ ] **L-6 (S)** Canary release plan with rollback criteria; first application onboarded as pilot. 
- [ ] **L-7 (S)** Walk through the **production readiness gate (section 18, rows 1-24)** and mark each row with evidence and owner. *Done when:* every row is closed or has a signed exception.

---

## Cross-cutting quality tasks (run alongside every phase)
- [ ] **T-1 (M)** Unit tests for `StorageService` with a fake GCS client (success, timeout, conflict on existing object, init failure) and for the rate limiter/auth edge cases. *Done when:* these modules reach the coverage target.
- [ ] **T-2 (S)** Unit tests for credential building (WIF file, provider flow, local modes, impersonation, production guard) with mocked `google.auth`. *Done when:* every branch covered.
- [ ] **T-3 (S)** Coverage gate in CI (start at 80% for `domain` and `application`, report-only elsewhere). 
- [ ] **T-4 (M)** Contract tests per port against all adapters (see F-5), run in CI on every pull request.
- [ ] **T-5 (M)** End-to-end smoke test in CI against emulators (upload → index → search → chat), replacing manual checks.
- [ ] **T-6 (S)** Dependency update routine: Dependabot/Renovate for backend and SDK, weekly; golden-set run required before merging model/SDK upgrades.

## Backlog (not scheduled; decide when needed)
- Data connectors and sync (SharePoint, Drive, Confluence, databases) with incremental updates and deletion propagation.
- User feedback loop (thumbs, corrections) feeding evaluation sets and curator queues.
- Semantic answer cache for public/FAQ KBs.
- Multi-region read path for the critical tier (ADR-008).
- Dedicated-index tier and self-service onboarding automation (section 19.12).

## Suggested order and parallel tracks

Assumption: a team of about 3-4 engineers plus part-time security/SRE. Adjust durations to your actual capacity; the **order** matters more than the weeks.

1. **Now (week 1):** SEC-1, SEC-2, SEC-3, H-5..H-8, H-13..H-16 (all small); run decisions D-1..D-8, D-11 in parallel.
2. **Weeks 2-3:** Phase 1 (F-0..F-9), H-17..H-20 (deployment hygiene), H-9..H-12 (state, CI); D-9 threat model.
3. **Weeks 3-5:** **Slice 0** (W-1..W-5) plus T-1..T-3. Review the demo before building more.
4. **Weeks 5-9:** Phase 2 (A-1..A-9) in parallel with Phase 3 infrastructure (I-1); start the real golden set (R-2) now because it is the longest-lead item.
5. **Weeks 8-12:** Phases 3-5 (ingestion, parsing/chunking, embedding/indexing) hardening what Slice 0 started.
6. **Weeks 11-15:** Phase 6 retrieval, then Phase 7 agents/skills/router/generation (G-1/G-2 can start after A-3; spike G-5b early).
7. **Weeks 13-17:** Phase 8 observability alongside Phase 7; Phase 9 caching only after baselines.
8. **Weeks 15-22:** Phase 10 portals (U-2 first), Phase 11 hardening, Phase 12 go-live gate.

Rule of thumb: never start a task whose `Dep` is open, and never tick a task without its evidence.
