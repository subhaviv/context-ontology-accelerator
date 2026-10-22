# Calanthir Pharmaceuticals — Design

**Spec status:** Draft v1 · 2026-10-21
**Implements:** `requirements.md` (same folder)
**Grounded in real code:** Beacon `engine/graph/` backend seam (`local_graph.py`,
`neptune_graph.py`), `packs/emed/seed.py`, `cdk/lambda/beacon_trigger.py`, `cdk/lib/*`;
COA serve `Query` (`POST /namespaces/{id}/query`) and the ontology Upload operations;
the Enterprise MCP Governance Gateway at
`/Users/subhaviv/AIFactory/sample-ai-agent-factory/enterprise-mcp-governance-gateway`
(`cdk/connectors/hcls_stack.py` = remote-HTTP-MCP-target pattern; Atlassian connector =
AgentCore-Runtime-target pattern).

> This document maps each requirement (R*/AC*) in `requirements.md` to a concrete
> implementation. Where a detail is still unverified it is marked **[verify]** rather
> than asserted.

## Reused vs. New (locked 2026-10-21)

**Reused — already deployed, NOT rebuilt:**
- **Beacon** — the existing agent. We add a backend + a workflow DB + a committer; we do
  not create a new agent.
- **COA serve runtime** `coa_dev_context_manager` — the human (JWT) resolver; the
  governed brain (Tier 1/2/3). Beacon does **not** touch this one.
- **COA control-plane API** — used for seeding (Phase 0) and the Upload-route commit
  (Phase 4). Not an MCP surface.

**New — the only things this spec builds:**
- **`coa-dev-serve-agent`** — a SECOND Context Manager runtime, same image as serve, with
  **IAM SigV4** inbound auth (no JWT authorizer). The system-caller door. Built + deployed
  + verified live this session (`infra/lib/stacks/services/serve-agent-stack.ts`).
- **`coa-dev-agent-mcp`** — an MCP-protocol runtime wrapper that validates a **Loom M2M
  `client_id`** (`allowedClients`) and SigV4-forwards to `serve-agent`. Built + deployed +
  verified live this session (`infra/lib/stacks/services/agent-mcp-stack.ts`); a
  CDK-managed resource policy locks `serve-agent` invoke to the wrapper role.
- `engine/graph/context_graph.py` — Beacon's `context` read backend (an MCP client of the
  **agent-mcp wrapper**, authenticated with a Loom M2M token).
- `beacon-workflow` DynamoDB table + write path.
- The Upload-route committer (control-plane API).

**Read path (DECIDED 2026-10-22 — dedicated M2M MCP wrapper, NOT the enterprise gateway):**
Beacon → **`coa-dev-agent-mcp`** (Loom M2M `client_id` auth) → **`coa-dev-serve-agent`**
(IAM SigV4) → COA serve orchestrator → COA Neptune. Beacon authenticates to the wrapper
with a **Loom M2M access token** minted from the `beacon-m2m` client
(`3eodl9tehbne84sosoqqvnhqf4`), its secret stored in the **consumer account**
(`beacon/coa-mcp-m2m`). **No cross-account IAM resource policy** — the two accounts share
only a Loom identity; the wrapper→serve-agent hop is same-account SigV4.

**Why NOT the enterprise MCP governance gateway (earlier draft):** the gateway path would
route Beacon through the shared enterprise gateway as a `CoaContext` target. We chose the
**dedicated wrapper** instead because (a) it was built and proven live end-to-end this
session (M2M token → MCP `initialize`+`tools/list` 200), (b) it keeps Beacon's governed
read on a purpose-built surface with its own M2M client + grant, independent of the shared
gateway's lifecycle, and (c) it needed no change to the enterprise gateway. The wrapper
runtime itself is MCP-protocol and runs the same `coa_mcp` server image, so Beacon still
gets the governed read tools (`query`, `graph_traversal`, `list_metrics`,
`describe_schema`, `translate_sparql`, `rag_retrieval`) — just via the dedicated wrapper,
not the gateway. **Write path:** Beacon → COA control-plane API (Upload route) — unchanged;
not an MCP tool.

## Identity model (resolved 2026-10-21 — read this before the read-path decision)

A long design pass settled how identity works across this system. The findings and the
locked decisions:

## Identity model (resolved 2026-10-21 — read this before the read-path decision)

A long design pass settled how identity works across this system. The findings and the
locked decisions:

**What COA recognizes (verified in code + docs):**
- COA's MCP/serve authorizes **human user identities only** — `User::{email}` and
  `Group::{cognito:groups}` → one of 7 built-in roles (`platform-admin/viewer`,
  `namespace-owner/maintainer`, `data-steward`, `data-analyst`, `default`). The live
  `coa-dev-user-pool` has exactly one group today (`Admin`→`platform-admin`).
- COA explicitly states **no machine-to-machine inbound credentials** for agent access
  (`agent-access.md`): "an agent carries the user's delegated token… never has its own
  standalone identity." The authorizer code (`coa_serve/auth.py`) keys on `sub` +
  resolves grants by `email`/groups — an M2M token has `sub` but no `email`/groups, so it
  resolves to **no grant (fail-closed)**.

**The identity of a background agent (principle):** an agent's identity is its **own
workload identity** (AgentCore issues one per runtime — COA's own MCP runtime has a
`workloadIdentityArn`). When acting for a human it mints a **fresh, short-lived** OBO
token from that workload identity — it does **not** store a human's refresh token.
Storing a user refresh token for a daemon is an anti-pattern (impersonation, long-lived
bearer secret, lying audit) and is **rejected**.

**What Beacon actually is (verified in `beacon_trigger.py`):** NOT a background poller —
it is **event-driven**. AgentCore **Memory** writes a PENDING record to
`/beacon/pending/session/{sessionId}/`; a Kinesis `MemoryRecordCreated` event fires the
trigger Lambda, which invokes Beacon once (idempotency-fenced). So every Beacon run
traces to a **Capture session**.

**The decisive rejection:** routing each Capture user's identity to COA would make
**every Capture user a COA user** — rejected as absurd and non-scaling. Capture users
(clinicians, meeting participants) are **users of Beacon, never of COA**. The only COA
users are the **few stewards/approvers**.

**LOCKED identity decisions:**
1. **Capture/end users are Beacon users, NOT COA users.** No per-end-user COA
   provisioning, ever.
2. **Beacon consumes COA as a SINGLE system** — one identity, not one-per-user. (An
   enterprise MCP being enterprise-grade means governed/audited/access-controlled, NOT
   that every employee has a login to it.)
3. **Only stewards/approvers are real COA users** (they curate the ontology and, if we
   wire it, approve commits) — a small bounded set.
4. **COA's lack of a first-class workload/system client is a real COA limitation** for
   this use case — the gap a managed AWS Context (workload identities) would close. We
   design around it, we do not force per-user provisioning to satisfy it.

**M2M feasibility (verified, for the record):** BOTH pools are on the **ESSENTIALS**
tier, which supports M2M access-token customization via a `V3_0` pre-token-generation
Lambda. The Loom pool (`us-east-1_NoErXe1QA`) already has a resource server
(`loom-user-pool-resource-server`), so the M2M scope scaffolding exists. This is what
makes agent access solvable **in Loom** (the IdP), not worked around in COA.

## COMMITTED ARCHITECTURE — Loom as the enterprise IdP (locked 2026-10-21)

**Loom is the enterprise IdP.** COA **federates to Loom** for all identities — human and
agent. The COA-created pool (`coa-dev-user-pool`) is **temporary**; once COA trusts Loom
end-to-end it is **retired/thrown away**. It exists today only because COA shipped with
its own pool.

**Two principals, both in Loom — and the WRITER is a human, not the agent:**

| Action | Actor | Identity | COA surface | Grant |
|---|---|---|---|---|
| **Read** (ground the signal, autonomous, pre-score) | **Beacon** (agent) | Loom **M2M** agent client | agent-mcp wrapper → serve-agent | `data-analyst` (read-only) |
| **Write** (commit the approved fact) | **Priya** (human, clicks Approve) | **Priya's own Loom user** token, live at approval time | control-plane REST (Upload route) | `data-steward` / ontology-manage |

**Key correction (2026-10-21): Beacon does NOT write. Priya's approval writes.** The
commit runs under Priya's **own live Loom token** at the moment she clicks Approve —
genuine pass-through of a present, authenticated human, NOT a stored credential and NOT
an agent write. COA audits the commit as `principalId = priya@…`, with her name + review
date on the fact. The Beacon **agent identity is read-only** — it never holds
ontology-write authority. This removes the privilege concern of an agent that can mutate
the enterprise graph.

**Surfaces (confirmed):** the COA **MCP server is read-only** — its only tools are the 6
reads (`list_metrics`, `describe_schema`, `query`, `translate_sparql`, `rag_retrieval`,
`graph_traversal`); there is **no MCP write tool**. So the **write cannot go through the
gateway/MCP** — it is a control-plane REST call (Upload route), governed by the
control-plane Cedar authorizer + Priya's grant, not by the gateway interceptors.

**COA changes to commit (Loom-IdP repoint — ~5 bindings + grants):**
1. Web app OIDC → Loom.
2. Serve runtime authorizer → Loom.
3. MCP runtime authorizer → Loom.
4. SSM `/coa/*` (issuer, pool-id, client-id, domain) → Loom; redeploy containers reading
   `JWT_ISSUER_URL`/`JWT_CLIENT_ID`.
5. Control-plane API authorizer → Loom.
6. Loom: add Beacon **M2M agent client** (read scope on the existing resource server) +
   a `V3_0` pre-token Lambda injecting `email`/`cognito:groups` for it.
7. COA grants: Beacon-agent group → `data-analyst` on `pharma`; approver group
   (Priya et al.) → `data-steward` on `pharma`; migrate the `Admin`→`platform-admin`
   seed to a Loom group.

**End state:** `coa-dev-user-pool` retired; Loom is the sole IdP; humans and the Beacon
agent both authenticate against Loom; COA authorizes by Loom group → COA role.

**[verify] at build:** the Beacon review screen authenticates Priya via Loom and can
forward her **live** token to the control-plane commit call (true pass-through). If the
UI records approval server-side without her token in hand, the commit must be made the
authenticated-as-Priya call the UI naturally issues.

---

## 0. Component Map

```
            signal
              │
              ▼
   ┌───────────────────────┐  MCP tool call      ┌─────────────────────────┐  SigV4      ┌────────────────────────┐
   │  Beacon runtime        │  (Bearer = Loom     │ coa-dev-agent-mcp        │  (same-acct)│  coa-dev-serve-agent    │
   │  (AgentCore)           │   M2M token)        │ MCP wrapper              │ ──────────▶ │  (IAM SigV4 auth, no    │
   │  engine/graph/         │ ──────────────────▶ │ authorizer:allowedClients│            │   JWT) — same image as  │
   │   context_graph.py     │  query /            │  =[beacon-m2m]; SCL_CM_  │            │   serve; runs the        │
   │  engine/signals.py     │  graph_traversal    │  SIGV4 forward           │            │   governed Tier 1/2/3    │
   │  (materiality score)   │                     └───────────────────────────┘            └──────────┬─────────────┘
   └─────────┬──────────────┘                                                                         │
             │ PENDING row                                                                            ▼
             ▼                                                                             COA Neptune (the
   ┌───────────────────────┐    approve      ┌───────────────┐   Upload route (control-plane)  ENTERPRISE graph)
   │ DynamoDB beacon-workflow│ ─────────────▶ │ committer      │ ───────────────────────────────▶ COA ontology
   │ (intake + approval)     │  (QA/Reg lead) │ (ingest fact)  │   (Priya's live Loom token)     calanthir-approved-facts
   └───────────────────────┘                 └───────────────┘
```

Beacon authenticates to the wrapper with a **Loom M2M token** (secret in the consumer
account); the wrapper validates the `client_id` and SigV4-forwards to `serve-agent` as a
system, which resolves the `Group::beacon-agents → data-analyst` grant and runs the
governed cascade. No enterprise gateway, no cross-account IAM policy.

**Design invariant:** COA Neptune is the only knowledge graph. `beacon-workflow`
(DynamoDB) holds transient workflow state only. Beacon's own `neptune_graph.py` backend
is **not** used for the enterprise graph in this design — the `context` backend reads
through the **dedicated agent-mcp wrapper → serve-agent**, and the enterprise write target
is COA's Upload route. The read is governed by serve-agent's own Cedar namespace gate +
grant resolution (the `beacon-agents → data-analyst` grant); the write is a direct
control-plane call under Priya's live Loom token (the MCP tool set is read-only — there is
no MCP commit tool).

---

## 1. Phase 1 design — Beacon reads COA (the `context` backend)

**Real seam:** Beacon already has `engine/graph/` with a backend abstraction and two
implementations (`local_graph.py`, `neptune_graph.py`). The reserved
`BEACON_GRAPH_BACKEND="context"` selects a **new** third implementation.

**New file:** `engine/graph/context_graph.py` implementing the same backend interface
`local_graph`/`neptune_graph` satisfy (same method surface the engine already calls —
**[verify]** the exact abstract methods by reading `engine/graph/__init__.py` /
`local_graph.py` before coding).

**What it does:**
- Is an **MCP client of the dedicated `coa-dev-agent-mcp` wrapper** (its runtime ARN in
  SSM `/coa/agent-mcp/runtime-arn`). It does **not** call COA's serve REST endpoint
  directly and does **not** go through the enterprise gateway.
- Authenticates with a **Loom M2M access token**: reads the `beacon/coa-mcp-m2m` secret
  from the consumer account, mints a `client_credentials` token
  (`client_id=3eodl9tehbne84sosoqqvnhqf4`, scope `loom/mcp:read`) from the Loom token
  endpoint, and sends it as `Authorization: Bearer` on the MCP calls. The wrapper's
  authorizer validates the `client_id` (`allowedClients`), then SigV4-forwards to
  `serve-agent`.
- Calls the COA MCP tools directly by their plain names (`query`, `graph_traversal`,
  `translate_sparql`, `rag_retrieval`, `list_metrics`, `describe_schema`) — no gateway
  target prefix, since there is no gateway in this path.
- Translates the engine's graph queries into those tool calls:
  - blast-radius / neighborhood traversal → `graph_traversal` (or `query`
    natural-language, `execute=true`, `mode="standard"` to stay under client timeout).
  - entity resolution (closed-set guard) → `query` returning class IRIs, which the
    adapter caches for the commit step.
- Returns results in the shape the engine expects (same DTOs `neptune_graph` returns),
  so `signals.py` is unchanged.

**Auth (DECIDED — Loom M2M, no gateway, no OBO):** Beacon mints a **Loom M2M token** from
the `beacon-m2m` client (secret in the consumer account) and presents it to the
**agent-mcp wrapper**, whose AgentCore authorizer validates `client_id` via
`allowedClients=[beacon-m2m]`. The wrapper maps the verified M2M identity to the system
profile (`userId=beacon-agent`, `groups=["beacon-agents"]`) and SigV4-forwards to
`serve-agent`; serve-agent's no-JWT branch resolves `Group::beacon-agents → data-analyst`
on the `pharma` namespace and runs the governed cascade. **No** user token, **no** OBO,
**no** cross-account IAM policy. Proven live this session: M2M `initialize`+`tools/list`
returned 200, and a direct no-JWT SigV4 invoke of serve-agent ran the full Tier-1/2/3
cascade under `groups=['beacon-agents']`.

**Underlying COA `Query` contract (confirmed, wrapped by the MCP `query` tool):**
`POST /namespaces/{namespaceId}/query` → body `{query, execute, mode, strategy,
timeoutMs, maxResults}`; response `{result: QueryResult, requestId, sessionId}`.

**AC1 mapping:** the adapter answers the §4 signal via a wrapper MCP tool call and
returns the grounded impact (sites, EU count, patient count) — no local graph consulted,
and the read is Cedar-authorized (serve-agent's namespace gate + the `beacon-agents →
data-analyst` grant).

---

## 1a. The dedicated M2M MCP wrapper + serve-agent (BUILT + DEPLOYED this session)

Instead of registering COA's MCP on the enterprise gateway, this design uses a
**dedicated read path** that was built, deployed, and proven live on 2026-10-22. Two new
runtimes in `agentops-publisher` (both from the same `coa_mcp`/serve container image):

**`coa-dev-serve-agent`** (`infra/lib/stacks/services/serve-agent-stack.ts`) — a second
Context Manager runtime, **IAM SigV4 inbound auth** (no `authorizerConfiguration` → IAM by
AWS default, verified live: `authorizerConfiguration=null`, status READY). Same image and
orchestrator as `serve`, its own deploy/rollback fate so it cannot affect the human serve
runtime. Writes its ARN to SSM `/coa/serve/agent-runtime-arn`. Runtime:
`coa_dev_context_manager_agent-7PMkexCgUR`.

**`coa-dev-agent-mcp`** (`infra/lib/stacks/services/agent-mcp-stack.ts`) — an
**MCP-protocol** runtime whose authorizer validates a Loom M2M `client_id`
(`allowedClients=[3eodl9tehbne84sosoqqvnhqf4]`, no `allowedAudience` — M2M tokens carry
`client_id`, not a user `aud`). Runs the `coa_mcp` server (`SCL_MCP_MODE=true`) with
`SCL_CM_SIGV4=true` so its execution tools **SigV4-sign** the Context Manager invoke
(the wrapper's own role) instead of forwarding a bearer token, and
`SCL_SYSTEM_PROFILE_GROUPS=beacon-agents` so the system profile is derived from the
verified M2M identity, not self-asserted. `CM_RUNTIME_ARN` points at `serve-agent`.
Runtime: `coa_dev_agent_mcp_server-LHhaJ29S2u`.

**Endpoint lock (CDK-managed):** a `ServeAgentEndpointLock` `AwsCustomResource` in
`agent-mcp-stack.ts` calls `putResourcePolicy` on serve-agent's DEFAULT endpoint, naming
the wrapper role as the sole `InvokeAgentRuntime` principal. It lives on the agent-mcp
stack (which already depends on serve-agent via SSM) to avoid a circular dependency, with
a policy fingerprint in the physical id so `onUpdate` re-fires on change. NOTE: a resource
policy is additive (union with identity policies), so within the same account a broadly
privileged role (e.g. Admin) can still invoke serve-agent — the policy is the
cross-account-correct admission grant + single-intended-caller record, not a same-account
Deny. Acceptable for dev; a strict same-account Deny is a pre-prod decision.

**Loom M2M client (BUILT this session):** `beacon-m2m` = `3eodl9tehbne84sosoqqvnhqf4` in
the Loom pool (`us-east-1_NoErXe1QA`), `client_credentials` flow, scope `loom/mcp:read`.
Its `{client_id, client_secret, token_endpoint, scope}` are stored as secret
`beacon/coa-mcp-m2m` in the **consumer** account (`172873868821`). The token endpoint is
`https://loom-agentops-195698601974.auth.us-east-1.amazoncognito.com/oauth2/token`.

**Grant (WRITTEN live):** RRM row `Group::beacon-agents → data-analyst` on the `pharma`
namespace (`36521996-76e9-433f-96cb-d2ec2db4e568`). Verified: serve's real
`resolve_profile` resolves it for a no-JWT `groups=['beacon-agents']` caller.

**Proven live (2026-10-22):** direct no-JWT SigV4 invoke of serve-agent ran the full
Tier-1/2/3 cascade under `groups=['beacon-agents']`; the M2M path (mint token → MCP
`initialize`+`tools/list`) returned 200 with all six `coa` read tools. The pharma
namespace is unseeded (`sourceCount:0`) today, so the governed answer is correctly "no
data" until Phase 0 runs — the auth/read path itself is proven.

**Build order note:** this `1a` is DONE. `context_graph.py` (§1) is the remaining
Beacon-side piece — an MCP client of `coa-dev-agent-mcp` using the `beacon/coa-mcp-m2m`
secret.

**Cross-account posture:** the wrapper→serve-agent hop is same-account
(`agentops-publisher`); Beacon→wrapper is a Loom JWT hop (not IAM), so **no publisher
resource names a consumer-account IAM principal** — the two accounts share only a Loom
identity. This was a hard requirement (user, 2026-10-21).

---

## 2. Phase 2 design — the workflow DB

**New CDK:** add a DynamoDB table to the Beacon CDK app (`cdk/lib/`, a new
`workflow_stack.py` or fold into an existing stack — **[verify]** which stack owns
app-state resources today; `memory_stack.py` is the closest analog).

**Table `beacon-workflow`:**
- PK `factId` (string, uuid).
- Attributes: `text`, `score` (number), `affectedEntityIRIs` (string set),
  `status` (`PENDING|APPROVED|REJECTED|COMMITTED`), `approver`, `note`, `reviewDate`
  (ISO, computed), `committedOntologyId`, `ingestJobId`, `createdAt`, `updatedAt`.
- GSI `status-index` on `status` (query the PENDING queue for the review screen).
- Billing: on-demand (demo-scale). PITR on (cheap, safe).

**Write path:** the signal handler (today `cdk/lambda/beacon_trigger.py` is the trigger
entrypoint — **[verify]** whether scoring runs there or in the runtime) scores via
`engine/signals.py` and `PutItem`s a `PENDING` row with the affected IRIs captured in
Phase 1.

**AC2 mapping:** one `PENDING` row per signal, carrying score + IRIs.

---

## 3. Phase 3 design — approval (QA/Regulatory)

**Read:** review screen lists `status=PENDING` via `status-index`, showing the
COA-grounded reason text stored on the row.

**Write:** approve → `UpdateItem` sets `status=APPROVED`, `approver`,
`note`, `reviewDate = today + <review window>` (computed from current date, never
hardcoded — standing rule). Reject → `status=REJECTED`. **No COA call in this phase.**

**Persona:** approver display defaults to "Dr. Elena Vasquez, QA & Regulatory Affairs"
(open decision in requirements §11). Approval is the governance checkpoint of the whole
design (the Upload route has no second COA-side review).

**AC3 mapping:** approve persists approver+note+reviewDate and transitions to APPROVED;
reject transitions to REJECTED.

---

## 4. Phase 4 design — commit to COA (Upload route)

**New module:** `engine/commit/coa_upload.py` (Beacon side), invoked when a row flips to
`APPROVED` (DynamoDB Stream → a small committer Lambda, **or** inline in the approval
handler — prefer the **stream-triggered committer** so a failed ingest can retry
independently of the approval request).

**Step 1 — render Turtle.** Build an RDF fragment asserting the approved fact against the
**existing COA class IRIs** cached in Phase 1. Example shape (illustrative; real IRIs come
from the induced `pharma` ontology):

```turtle
@prefix coa:  <https://coa.calanthir.example/ns#> .
@prefix prov: <http://www.w3.org/ns/prov#> .

coa:fact_<factId> a coa:ApprovedFact ;
    coa:subject      <IRI:Supplier/KingsportAPILabs> ;
    coa:predicate    coa:supplyDelayImpacts ;
    coa:object       <IRI:Trial/NUV-301> ;
    coa:statement    "XR-7 supply delay impacts NUV-301 continuity" ;
    coa:approvedBy   "Dr. Elena Vasquez" ;
    coa:reviewDate   "<computed-date>"^^xsd:date ;
    coa:source       "Beacon" .
```
**[verify]** the exact prefix/predicate vocabulary COA expects for an uploaded
`user_created` ontology to merge cleanly (read a sample induced ontology via
`DownloadOntology` on the `pharma` namespace after Phase 0, and match its prefixes).

**Step 2–5 — Upload route (confirmed contract):**
1. `POST /namespaces/{pharma}/ontologies/upload-url`
   `{filename, ontologyId:"calanthir-approved-facts", title}` → `{uploadUrl, s3Key, ontologyId}`.
2. `PUT uploadUrl` with the Turtle bytes.
3. `POST /namespaces/{pharma}/ontologies/calanthir-approved-facts/ingest-from-s3`
   `{s3Key, format:"turtle", ontologyType:"user_created", title}` → `202 {jobId, ontologyId, status}`.
4. Poll `GET …/ingest-status/{jobId}` until `COMPLETED`
   (`QUEUED→RUNNING→EMBEDDINGS_SYNC→COMPLETED`).
5. `UpdateItem`: `committedOntologyId`, `ingestJobId`, `status=COMMITTED`.

**Idempotency / retry:** ingest is keyed to a fresh `s3Key` per attempt; a failed/partial
ingest leaves the row `APPROVED` (not `COMMITTED`) so the committer retries. The approved
fact's `factId` makes the Turtle deterministic, so re-ingest replaces rather than
duplicates (merge by IRI — last writer wins on the fact node).

**AC4 mapping:** on approval, fact is ingested into `calanthir-approved-facts`;
`GetIngestStatus=COMPLETED`; row `COMMITTED` with ids.

### Accepted constraints (carried from requirements §6)
- Async fragment ingest, not a synchronous single-triple write (seconds + poll).
- Committed facts are `is_mapped=false` → visible to graph traversal / semantic search,
  not to R2RML Tier-2 NL→SQL. Server-derived; cannot be forced.
- Upload route bypasses COA's proposal/validate/accept review by design; Beacon's
  approval is the governance gate.

---

## 5. Phase 0 design — seed the enterprise graph

**Data generation:** a script `specs/calanthir-pharmaceuticals/gen_data.py` (scratch
output under `$KIROCREW_SCRATCH`, final CSVs committed under the spec folder or staged
only to S3 — **decide**) emits CSVs with real PK/FK columns so induction infers the §3
relationships:
- `suppliers.csv` (supplier_id PK, name, country)
- `materials.csv` (material_id PK, name, supplier_id FK)
- `batches.csv` (batch_id PK, material_id FK, lot, status)
- `drugs.csv` (drug_id PK, name, batch_id FK)
- `trials.csv` (trial_id PK, name, phase, drug_id FK)
- `trial_sites.csv` (site_id PK, trial_id FK, region_id FK, enrolled)
- `regions.csv` (region_id PK, name)

**Staging + source registration:**
1. Create S3 bucket (e.g. `calanthir-pharma-seed-195698601974`); upload CSVs
   (**the `aws s3 sync`/`cp` of local files is handed to the user's terminal** — blocked
   from the agent session by policy; agent provides exact paths + destination).
2. Tag bucket / Glue DB `coa:namespace=<pharma-ns-id>`.
3. Create a Glue database + tables over the S3 prefixes (crawler or explicit table defs)
   so they are Athena-queryable (Glue source prerequisite).
4. `CreateNamespace` `pharma` (control-plane API, Cognito-authed).
5. Register a **Glue Data Catalog** source → scan → review → `StartInduction` →
   `AcceptProposal`.

**AC0 mapping:** COA holds the 7 classes + §3 relationships; a graph query returns the
Kingsport→Material→Batch→Nuvora→NUV-301→sites chain.

---

## 6. Phase 5 design — verify

A verification script issues `graph_traverse`/`Query` for the committed fact and asserts
the provenance literals are present. Demo renders before (no fact) / after (fact with
`approvedBy`/`reviewDate`).

---

## 7. Identity & Security (design-level)

- Beacon runtime → **agent-mcp wrapper**: a **Loom M2M token** (`Authorization: Bearer`),
  minted from the `beacon-m2m` client whose secret lives in the consumer account
  (`beacon/coa-mcp-m2m`). The wrapper validates the `client_id` (`allowedClients`), maps
  it to the system profile, and **SigV4-forwards** to serve-agent using its own role, so
  Beacon never holds a COA/SigV4 credential and serve-agent never sees a user token.
  Verified live: the wrapper's `allowedClients` contains `3eodl9tehbne84sosoqqvnhqf4`, and
  serve-agent's endpoint policy admits only the wrapper role. No enterprise gateway, no
  OBO, no cross-account IAM policy.
- DynamoDB encrypted (AWS-managed KMS min; CMK if PHI-adjacent — pharma trial/patient
  counts are aggregate, not PHI, so AWS-managed is acceptable **[verify with user]**).
- No secrets in code; any static key path follows the existing Beacon `.env` (gitignored)
  convention.

---

## 8. Testing

- **Unit:** `context_graph.py` adapter against a mocked COA `Query` response; Turtle
  renderer golden-file test; score→row mapping.
- **Integration (needs deployed COA):** Phase 0 seed smoke (induce + query chain);
  end-to-end loop test (signal → PENDING → approve → COMMITTED → verify in graph).
- In-memory doubles only under `tests/` (standing rule: no local engines in product path).

---

## 9. Build Order (maps requirements §9)

1. Phase 0 seed (gen data → S3 → Glue → namespace → induce).
2. Phase 1a — **DONE this session:** `serve-agent` (IAM) + `agent-mcp` wrapper (Loom M2M
   `allowedClients`) deployed + verified live; `beacon-m2m` client + consumer-account
   secret + `beacon-agents → data-analyst` grant in place.
3. Phase 1 `context_graph.py` read adapter — an **MCP client of the agent-mcp wrapper**
   (mint Loom M2M token from `beacon/coa-mcp-m2m` → MCP `tools/call`) + Beacon runtime
   role grant `secretsmanager:GetSecretValue` on that secret.
4. Phase 2 DynamoDB table + write path.
5. Phase 3 review-screen approve/reject wiring.
6. Phase 4 Upload-route committer (stream-triggered).
7. Phase 5 verification.

---

## 10. Open [verify] items (resolve before/at each phase, not by guessing)

- `engine/graph/` abstract backend method surface (read before writing `context_graph.py`).
- Whether scoring lives in `beacon_trigger.py` or the runtime.
- Which CDK stack should own `beacon-workflow`.
- COA uploaded-ontology prefix/predicate vocabulary (via `DownloadOntology` after Phase 0).
- **context_graph.py (§1):** map each engine signal query to a wrapper MCP `tools/call`
  (`query`/`graph_traversal`) and parse the result into the scorer's signal DTOs; grant
  Beacon's runtime role `secretsmanager:GetSecretValue` on `beacon/coa-mcp-m2m`
  (same-account, consumer).
- **serve-agent endpoint lock:** a strict same-account Deny (block all but the wrapper
  role, incl. Admin) is a pre-prod decision — the current policy is additive, so broadly
  privileged same-account roles can still invoke serve-agent in dev.
- DynamoDB KMS choice (AWS-managed vs CMK) — confirm with user.
