# Local AI implementation plan

**Goal:** Deliver the approved AGX-only local assistant, private seed knowledge, incremental repair experience, and controlled integrations/automations.
**Spec:** [Design](2026-10-04-local-ai-design.md).
**Execution:** Root owns API/data/import/integration; independent workers own host runtime and web UI. User explicitly requested autonomous completion; no repeat permission gates. Current writable checkout uses branch `codex/local-ai` (managed worktrees were not writable in this session).

## Global constraints

AGX identity + NVMe + CUDA checks before installation/execution; no downloads/inference on Khadas. Unsigned hash-verified OTA v1 remains unchanged. No private corpus in Git/OTA. Existing scopes, photo/operator-review workflow and encryption preserved. No eight-hour soak. AI can propose but cannot approve/enable its own mutations. Run unsafe scripts only in the host Docker sandbox; never `exec` in API/host process.

## Shared interfaces

Host projection `/ops/ai-public/ai-runtime.json` (read-only directory mount, so atomic refreshes remain visible): schema=1, supported/installed/enabled/ready booleans, reason nullable string, model string, backend cuda|null, model_sha256 nullable. API Settings: `ai_runtime_state_path`, `ai_broker_socket`. Broker UDS `/run/robopark-ai/broker.sock`: GET /health; POST /v1/chat/completions; POST /sandbox {source,input} -> {output,stdout}; POST /control {action:install|enable|disable|remove_model}. No secret appears in projection.

API `/ai` JSON contract (snake_case; errors use detail string):
- GET status -> supported,installed,enabled,ready,reason,model,backend,can_manage,counts:{documents,candidates,jobs}.
- GET/PATCH config -> {enabled,learning_enabled,revision}; PATCH includes revision. POST runtime {action} -> status.
- GET prompts -> [{role,content,revision}]; PUT prompts/{role} {content,revision}; roles mechanic/operator/admin. Fixed safety policy cannot be overridden.
- GET documents?q=&park_id=&state=&offset=0&limit=30 -> {items:[{id,title,kind,state,trust,park_id,source_ref,updated_at,revision}],total,offset,limit}; GET documents/{id} adds content. POST {title,content,kind,park_id?,state?}; PATCH {revision,title?,content?,state?}; DELETE tombstones. kind manual/chat/ticket/note, state active/candidate/rejected/deleted, trust instruction/experience/unverified. POST documents/import {documents:[{title,content,kind,source_ref}],park_id:null|int,activate_manuals:bool,activate_unverified:bool} max100 per request -> {created,duplicates,rejected}; import errors reject whole batch. Frontend reads private .jsonl/.json max64MiB and submits batches; repeat safe.
- GET conversations -> [{id,title,park_id,issue_key,updated_at}]; POST {title?,park_id,issue_key?}; GET conversations/{id} -> conversation + messages:[{id,role,content,sources:[{id,title,excerpt,trust}],created_at}], jobs:[Job]. DELETE conversation removes its own records.
- POST conversations/{id}/messages {content,idempotency_key} -> Job. Job={id,kind,state,created_at,updated_at,error,result}; queued/running/succeeded/failed/cancelled. GET jobs/{id}; POST jobs/{id}/cancel.
- GET/POST connectors; PATCH/DELETE connectors/{id}. Input {name,url,method,token?,enabled?}; HTTPS fixed host and endpoint (only `{{issue_key}}` in path; no redirect), method GET/POST/PUT/PATCH; encrypted bearer token, response exposes token_set only. Configuring external service is explicit admin action; private network destinations are initially rejected. No arbitrary URL provided to model or script.
- GET/POST scripts; PATCH/DELETE scripts/{id}. {id,name,source,revision,enabled,tested_revision,updated_at}; write {name,source,revision?}. POST scripts/{id}/test {input:any} -> Job; server test success ties to exact revision. PATCH enabled true requires tested_revision=revision. Edits disable script and revoke prior test. Generated scripts have function main(data) -> JSON.
- POST drafts {kind:script|automation,instruction,park_id} -> Job; result {source?,proposal?,explanation}. Drafts do not create/enable executable entities; UI presents result for explicit save.
- GET/POST automations; PATCH/DELETE automations/{id}. {id,name,park_id,enabled,revision,filters:{component_ids:string[],defect_codes:string[],keywords:string[]},action:{connector_id?:string,script_id?:string,body:JSON},updated_at}; POST writes disabled; PATCH changes require revision and disable unless only toggling enabled. At least script or connector. POST automations/{id}/preview {event:JSON} -> {matches,payload}; no external request. Enabled rule consumes only future confirmed-close events, unique rule/event key; changed or disabled before execution cancels pending runs.
- GET runs?limit=50 -> [{id,automation_id,event_key,state,error,created_at,updated_at,result}]; DELETE runs?before_days=30 -> {deleted}; POST maintenance {kind:history|failed_jobs,before_days:30} -> {deleted}. Disabled data is not silently reactivated.

## Tasks and verification

- [x] 1. Data migration/models/schemas/policy: scope, ownership, unsupported gates, default prompts, idempotency/CAS, config. RED access and migration tests → implementation → focused tests. Files ai_models.py, ai_schemas.py, services/ai/{policy,store}.py, routers/ai.py, alembic0057.
- [x] 2. Knowledge store/import/search/learning: indexed token/chunk search (scope before ranking), redaction, deterministic dedupe/tombstones, closed-cycle replay-safe ingestion. Tests cross-park isolation, instruction injection inert, duplicate and delete/reimport, no unsent repair learned. Private converter scripts/build_knowledge_seed.py reads supported files without executing instructions, emits ignored JSONL + manifest; inspect representative PDFs using PDF skill.
- [x] 3. Chat/prompt/queue: durable jobs, single worker, no DB connection during inference, correct one-system-message prompt, bounded sources/history/output, validated citations, cancel/revocation/timeout. Tests fake UDS completion, malformed/empty/truncated output and denied scopes. Files services/ai/{prompts,runtime,jobs}.py + loop integration.
- [x] 4. Scripts/connectors/automation: encrypted credentials, DNS pin/SSRF defense, revisions and tested-only activation, bounded deterministic event filters/template, sandbox-only scripts, non-repeating uncertain API delivery. Tests deletion/disable races, malicious URLs, field/template types, repeated closure, injected rule proposal powerless.
- [x] 5. Host provisioning/broker owned by host worker: pinned Prism source/PQ2_0 hashes, NVMe/RAM/CUDA/board gates, private services, fail-soft install, stop/remove/reinstall, sandbox isolation, OTA preservation. Focused host tests and rendering. Physical CUDA smoke required before ready.
- [x] 6. Web owned by UI worker: assistant chat/knowledge CRUD+batch import; admin prompts/config/connectors/scripts/drafts/rules/log cleanup; status-gated route and ticket shortcut. Tests roles/status, retry drafts, source rendering, explicit rule activation, mobile/axe.
- [x] 7. Integration/release: full standard API/web gates, focused browser route+workflow, migration and OTA/governance; independent review and fixes. Update README/runbook/release metadata, prepare private seed, commit and publish tested main. Do not claim deployment or hardware acceptance absent actual target.

## Review focus

- User loses park or role while response pending: no stale answer/source disclosure or action execution.
- Imported text says to send secrets/run commands: treated only as quoted evidence, cannot change policy.
- Delete source or disable rule while job pending: retrieval and execution revalidate authoritative state.
- Network timeout after external PATCH: uncertain recorded, no automatic duplicate retry.
- Power loss/restart during download/generation: verified atomic model install, interrupted jobs visible, no repeated learned event.

## Progress ledger

Planning: design/contract self-reviewed; decisions follow prior approved requirements and autonomous instruction. Baseline 6afbb5c9. Implementation completed in rc.24. Host and web workers implemented their isolated areas; independent host/API reviews were resolved before release. The private seed contains 12,598 documents (344 manuals, 4,180 tickets, 1,500 chat segments, 6,574 notes), with deduplicated ticket renderings and an ignored manifest. It remains outside Git/OTA.

Confirmed-close events are recorded durably even if the host projection is temporarily absent; RAG ingestion and rule execution remain AGX-only. Approval captures the park before releasing the claim, and learning requires both successful local operator approval and confirmed external closure.

Verification evidence and remaining hardware acceptance are recorded in [the release review](../reviews/2026-10-04-local-ai.md). Completion of implementation does not mean physical AGX acceptance: native CUDA build, model smoke, throughput, peak shared memory, and Wi-Fi recovery must still be checked on GEACX1. No eight-hour soak was run.
