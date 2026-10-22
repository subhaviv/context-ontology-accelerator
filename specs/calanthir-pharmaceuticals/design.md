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
- **COA MCP runtime** `coa_dev_mcp_server` — the governed front door with the read tools
  (`query`, `graph_traverse`, `translate`, `rag_retrieval`, `list_metrics`,
  `describe_schema`).
- **COA serve runtime** `coa_dev_context_manager` — the resolver the MCP runtime
  delegates to; Beacon does **not** call it directly.
- **COA control-plane API** — used for seeding (Phase 0) and the Upload-route commit
  (Phase 4). Not an MCP surface.
- **Enterprise MCP Governance Gateway** — the single governed MCP endpoint Beacon speaks
  to; Cedar authz + audit + interceptors apply.

**New — the only things this spec builds:**
- `engine/graph/context_graph.py` — Beacon's `context` read backend (an MCP client of
  the gateway).
- `beacon-workflow` DynamoDB table + write path.
- The Upload-route committer (control-plane API).
- One new gateway target registering the COA MCP server + its Cedar read permits.

**Read path (corrected):** Beacon → **Enterprise MCP Governance Gateway** → COA MCP
runtime → COA serve runtime → COA Neptune. Beacon does NOT call COA's serve REST endpoint
directly; it calls MCP tools through the gateway. **Write path:** Beacon → COA
control-plane API (Upload route) — not through the gateway, not an MCP tool.

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
| **Read** (ground the signal, autonomous, pre-score) | **Beacon** (agent) | Loom **M2M** agent client | MCP server via gateway | `data-analyst` (read-only) |
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
   ┌───────────────────────┐   MCP tool call     ┌─────────────────────────┐   fronts    ┌────────────────────────┐
   │  Beacon runtime        │  (Bearer JWT, M2M)  │ Enterprise MCP Governance │  (target)  │  COA MCP runtime        │
   │  (AgentCore)           │ ──────────────────▶ │ Gateway                   │ ─────────▶ │  coa_dev_mcp_server     │
   │  engine/graph/         │  query /            │ (Cedar authz + audit +    │            └──────────┬─────────────┘
   │   context_graph.py     │  graph_traverse     │  interceptors)            │                       │ delegates
   │  engine/signals.py     │                     └───────────────────────────┘                       ▼
   │  (materiality score)   │                                                            COA serve runtime →
   └─────────┬──────────────┘                                                            COA Neptune (the
             │ PENDING row                                                               ENTERPRISE graph)
             ▼                                                                                  ▲
   ┌───────────────────────┐    approve      ┌───────────────┐   Upload route (control-plane)  │
   │ DynamoDB beacon-workflow│ ─────────────▶ │ committer      │ ───────────────────────────────┘
   │ (intake + approval)     │  (QA/Reg lead) │ (ingest fact)  │   → COA ontology calanthir-approved-facts
   └───────────────────────┘                 └───────────────┘
```

**Design invariant:** COA Neptune is the only knowledge graph. `beacon-workflow`
(DynamoDB) holds transient workflow state only. Beacon's own `neptune_graph.py` backend
is **not** used for the enterprise graph in this design — the `context` backend reads
through the governed gateway, and the enterprise write target is COA's Upload route.
Reads are governed (gateway Cedar + audit); the write is a direct control-plane call
(the MCP tool set is read-only — there is no MCP commit tool).

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
- Is an **MCP client of the Enterprise MCP Governance Gateway** (single governed MCP
  endpoint, published to SSM `/enterprise-mcp-gateway/gateway/url`). It does **not** call
  COA's serve REST endpoint directly.
- Calls the COA MCP tools, which surface on the gateway as `<TargetName>___<tool>`
  (triple underscore) — with target name `CoaContext` (see §1a), that is
  `CoaContext___query`, `CoaContext___graph_traverse`, `CoaContext___translate`, etc.
- Translates the engine's graph queries into those tool calls:
  - blast-radius / neighborhood traversal → `CoaContext___graph_traverse` (or
    `CoaContext___query` natural-language, `execute=true`, `mode="standard"` to stay
    under client timeout).
  - entity resolution (closed-set guard) → `CoaContext___query` returning class IRIs,
    which the adapter caches for the commit step.
- Returns results in the shape the engine expects (same DTOs `neptune_graph` returns),
  so `signals.py` is unchanged.

**Auth (per requirements §7):** Beacon presents a **Cognito JWT to the gateway** (the
gateway is `authorizerType=CUSTOM_JWT`); the gateway runs Cedar authz + audit +
interceptors, then invokes the COA MCP target using the gateway's own outbound
credential (see §1a). Beacon carries `session.id` for correlation; **no** user token,
**no** OBO. **[verify]** Beacon's M2M client id is in the gateway's `allowedClients`.

**Underlying COA `Query` contract (confirmed, wrapped by the MCP `query` tool):**
`POST /namespaces/{namespaceId}/query` → body `{query, execute, mode, strategy,
timeoutMs, maxResults}`; response `{result: QueryResult, requestId, sessionId}`.

**AC1 mapping:** the adapter answers the §4 signal via a gateway MCP tool call and
returns the grounded impact (sites, EU count, patient count) — no local graph consulted,
and the call is Cedar-authorized + audited at the gateway.

---

## 1a. Register the COA MCP server as a governed gateway target

**Pattern selection (verified):** The gateway supports two remote-MCP-target shapes:
- **Static public HTTPS MCP** (`hcls_stack.py`): a plain declarative `CfnGatewayTarget`
  with `McpTargetConfigurationProperty(mcp_server=McpServerTargetConfigurationProperty(
  endpoint="https://…/mcp", listing_mode="DEFAULT"))`. Simple, no credential provider.
- **AgentCore Runtime MCP** (Atlassian connector): the endpoint is a **Runtime
  invocation URL derived from the runtime ARN** (url-encoded), needs a credential
  provider, and is wired via the connector's **custom-resource** path, not the plain
  declarative target.

**COA's MCP is the Runtime shape, not the static one** — confirmed:
`McpRuntimeArn = arn:aws:bedrock-agentcore:us-east-1:195698601974:runtime/coa_dev_mcp_server-MkoY1fCoME`.
So registering it follows the **Atlassian/Runtime pattern**, not the simpler HCLS one.
This is a real wrinkle, not a one-line config: the docstring in `hcls_stack.py` says the
static path is only for a "STATIC public https URL … NOT a Runtime invocation URL."

**Steps to add COA as a target (new stack `cdk/connectors/coa_stack.py`, modeled on the
Atlassian connector):**
1. Read the gateway's `gateway/id`, `gateway/arn`, `policy-engine/id` from SSM (deploy
   order: core gateway stack first).
2. Build the COA MCP **Runtime invocation endpoint** from `McpRuntimeArn` (url-encode the
   ARN into the AgentCore runtime MCP URL form — **[verify]** exact URL template against
   the Atlassian connector's code).
3. Create the target: `CfnGatewayTarget` with
   `targetConfiguration.mcp.mcp_server.endpoint=<runtime MCP url>`,
   `listing_mode="DEFAULT"` (discover + cache COA's tools).
4. **Outbound auth:** COA's MCP runtime requires inbound JWT, so the target needs a
   **credential provider** so the gateway can authenticate to COA. **[verify]** whether
   to use the gateway's `GATEWAY_IAM_ROLE` + a resource policy on the COA runtime, or an
   OAuth client-credentials provider against COA's Cognito pool (the Atlassian connector
   uses an OAuth credential provider; COA is M2M, so a `client_credentials` provider
   against COA's pool is the likely fit). Target name: `CoaContext`.
5. **Cedar permits:** one `CfnPolicy` per COA tool Beacon uses
   (`CoaContext___query`, `CoaContext___graph_traverse`, `CoaContext___translate`),
   `mode: ENFORCE` — a tool with no permit is denied-by-default AND hidden from
   `tools/list`. Scope the permit to the QA/Reg principal/role, or an unconditional read
   permit for authenticated gateway users (decide the posture; pharma context is not PHI).
6. Deploy the new connector stack; verify `tools/list` through the gateway shows the
   `CoaContext___*` tools and a `tools/call` succeeds.

**Cross-account note:** the gateway and COA are both in `agentops-publisher`
(195698601974), so this is same-account — no cross-account resource policy needed, unlike
some earlier work. **[verify]** the gateway stack's account at deploy time.

**[verify] items for 1a:** exact Runtime-MCP URL template; credential-provider choice
(IAM role + runtime resource policy vs. Cognito client-credentials); whether the gateway
account == COA account.

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

- Beacon runtime → **gateway**: Cognito JWT (`Authorization: Bearer`), `session.id`
  baggage for correlation. The **gateway → COA MCP** hop uses the gateway's own outbound
  credential (§1a), so Beacon never holds a COA token. **[verify]** Beacon's client id is
  in the **gateway's** `allowedClients`; and COA's MCP runtime accepts the gateway's
  outbound credential. If a config gap is on the COA side, escalate as a decision — do
  not silently edit COA stacks.
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
2. Phase 1a register COA MCP as a gateway target (`coa_stack.py`, Runtime pattern) +
   Cedar permits; verify `CoaContext___*` tools list through the gateway.
3. Phase 1 `context_graph.py` read adapter (MCP client of the gateway) + auth.
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
- **Gateway target (§1a):** exact Runtime-MCP URL template (from the Atlassian connector);
  credential-provider choice (gateway IAM role + COA runtime resource policy vs. Cognito
  client-credentials provider against COA's pool); gateway account == COA account.
- **Gateway `allowedClients`** includes Beacon's JWT client; COA MCP runtime accepts the
  gateway's outbound credential.
- Cedar permit posture for `CoaContext___*` (scoped to QA/Reg role vs. unconditional read).
- DynamoDB KMS choice (AWS-managed vs CMK) — confirm with user.
