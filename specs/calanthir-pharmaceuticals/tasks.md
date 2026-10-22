# Calanthir Pharmaceuticals — Implementation Tasks

**Implements:** `design.md` (committed Loom-IdP architecture, 2026-10-21)
**Naming (fixed):** **Loom** = the enterprise IdP (Cognito pool `us-east-1_NoErXe1QA`,
the user/identity directory). **Beacon** = our agent. COA **federates to Loom** and
retires its own pool (`coa-dev-user-pool`).

## DECISION (2026-10-21): drop Set B (agent M2M), keep Set A (repoint)

**Verified blocker:** a Cognito pre-token Lambda **cannot** make an M2M (client-credentials)
token carry the two claims COA validates — **`aud`** is not overridable, and
**`cognito:groups` is user-identity only (not machine)**. An M2M token can only get
`scope` + arbitrary custom claims. So COA (which checks `aud` + `cognito:groups`) cannot
authorize a Beacon M2M token without COA-side code changes. **We are NOT doing that.**

**What we ARE doing:**
- **Set A — repoint COA to the Loom pool (DO THIS).** Serves the **humans**: stewards and
  approvers (Priya) log into COA with their **Loom** identity; Priya's commit (the write)
  runs under her **own live Loom user token**. One enterprise IdP for all COA *human*
  access; the COA pool is retired.
- **Set B — agent M2M into COA: DROPPED.** Beacon does NOT authenticate into COA's MCP.
- **Beacon's READ goes DIRECT** (supersedes Sets C/D's gateway-MCP read): Beacon reads the
  graph as a system via its own IAM/role path (COA serve runtime or Neptune directly),
  with **no COA MCP auth**. The governed-MCP story remains for *human* analysts/stewards.

So: **COA↔Loom repoint = human identity plane. Beacon read = direct, no COA token.
Beacon write = Priya's human Loom token at approval.**

Task Set A is the active work. Sets C–H are revised per the direct-read decision (see each
set's note).

---

## TASK SET A — Repoint COA to federate to Loom

> Goal: every COA auth surface trusts the **Loom** pool instead of `coa-dev-user-pool`.
> Humans log into COA with their Loom identity; the COA pool becomes retireable.
> All changes are on COA-side resources — recorded as a deliberate, consented change to
> the deployed COA (not silent). Prefer editing COA's **CDK source** so a later
> `cdk deploy` does not revert a raw CLI change; CLI only for verify/one-offs.

- [ ] **A0. Snapshot current state (rollback safety).** Capture current authorizer
  configs, SSM `/coa/*` values, and web-app OIDC config to a file under
  `$KIROCREW_SCRATCH`, so every binding can be reverted. Record the 2 existing
  `coa-dev-user-pool` users (they will be stranded — confirm that is acceptable).
- [ ] **A1. Locate the pool bindings in COA's CDK.** ✅ DONE (2026-10-21). FINDING:
  the repoint is **not** 5 independent edits — COA's CDK has **first-class external-OIDC
  support** (`foundation/idp-authentication-stack.ts`: `idpType=OIDC` → "no Cognito;
  stores external issuer/clientId/jwksUri in SSM", plus `oidcProviders` federation).
  Every consumer reads the trust anchor from **SSM `/coa/*`** (single source of truth):
  `mcp-stack.ts`/`serve-stack.ts`/control-plane all do
  `ssm.StringParameter.valueForStringParameter('/coa/issuer' | '/coa/userpool-client-id'
  | '/coa/mcp-client-id' | '/coa/authentication-group-token-name')`, feeding BOTH the
  AgentCore `customJWTAuthorizer` (`${issuer}/.well-known/openid-configuration`) AND the
  container env `JWT_ISSUER_URL`/`JWT_CLIENT_ID`. So A3–A7 collapse to: **set the IdP
  context to OIDC/Loom → `cdk deploy`** (all stacks re-read SSM and repoint together).
  NOTE: `mcp-stack.ts` uses `allowedAudience` (ID-token `aud`), `allowedClients` omitted.
  We **keep it that way** — COA is unchanged. Per AWS docs, if both `allowedClients` and
  `allowedAudience` were set they'd be **AND**ed (a human ID token has `aud`/no
  `client_id`, an M2M token has `client_id`/no `aud`, so they could not share one
  authorizer). Instead the **V3 pre-token Lambda shapes Beacon's M2M token to carry a
  valid `aud`** (see Set B), so COA's existing `aud` authorizer accepts it like any ID
  token. No `allowedClients` change on COA.

- [ ] **A2. Read the OIDC/IdP context shape + author Loom values.** ✅ DONE (2026-10-21).
  FINDINGS:
  - **`OidcSettings`** (infra/lib/types.ts) = `{ issuerUrl, clientId, jwksUri?,
    groupClaim? }`. Setting `idpType=OIDC` + `oidcSettings` makes the OIDC branch write
    SSM `/coa/{userpool-client-id, mcp-client-id, issuer, authentication-group-token-name,
    jwks-uri?}` — the single lever that repoints all consumers on `cdk deploy`.
  - **OIDC mode provisions NO Cognito pool** ("OIDC → no Cognito"). So switching to OIDC
    doesn't just retire `coa-dev-user-pool` — it is **never created** in OIDC mode. This
    IS the "throw away the COA pool" end-state, reached natively.
  - **🔴 BLOCKER / required COA extension:** in OIDC mode `OidcSettings` has a SINGLE
    `clientId`, reused for BOTH web and MCP (`this.mcpClientId = oidc.clientId`), per the
    code's own TODO ("accept it via oidcSettings.mcpClientId"). We need TWO Loom clients
    (human web + Beacon M2M), so we must **extend `OidcSettings` with an optional
    `mcpClientId` / `allowedClients[]`** (the sanctioned TODO extension) OR add
    `allowedClients` directly in `mcp-stack.ts`. Folds into Set B's mcp-stack change.

  **Loom clients (live, both already exist — most of Set B scaffolding is present):**
  | Purpose | Client name | Client id | Flow | Secret |
  |---|---|---|---|---|
  | Human web / approver | `loom-user-pool-user-client` | `3o0q6hdtfo1sphqq7jtqaspb3f` | code+PKCE | no |
  | Beacon agent (M2M) | `loom-user-pool-m2m-client` | `752qo4e554h96dj9352p930llb` | client_credentials | yes |

  Resource server `loom` already has `mcp:read`/`agent:read` scopes (M2M client currently
  scoped only to `loom/invoke` → add `loom/mcp:read`). Loom groups include
  `clinical-staff`, `t-admin`, `g-admins-*`, `g-users-*`.

  **AUTHORED Loom `oidcSettings` for COA (to set in cdk.json context / SsmConfig):**
  ```jsonc
  {
    "idpType": "OIDC",
    "oidcSettings": {
      "issuerUrl": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_NoErXe1QA",
      "clientId":  "3o0q6hdtfo1sphqq7jtqaspb3f",   // human web/approver client
      "groupClaim": "cognito:groups"
      // jwksUri omitted → derived from issuer
      // mcpClientId: "752qo4e554h96dj9352p930llb"  // REQUIRES the OidcSettings extension (A2 blocker)
    }
  }
  ```
  **[verify] before deploy:** add the human web client's callback URL
  (`https://d3upg0x9odcmsc.cloudfront.net/authenticate/`) to the Loom user client; confirm
  Loom user tokens carry `cognito:groups`.
- [ ] **A3. Repoint the web app OIDC** → Loom issuer + Loom web client; update callback
  URLs on the Loom client to COA's web-app URL (`d3upg0x9odcmsc.cloudfront.net/...`).
- [ ] **A4. Repoint the serve runtime authorizer** (`coa_dev_context_manager`) →
  Loom `discoveryUrl`; set `allowedClients`/`allowedAudience` to the Loom client(s).
- [ ] **A5. Repoint the MCP runtime authorizer** (`coa_dev_mcp_server`) → Loom
  `discoveryUrl`, `allowedAudience=[Loom web client id]` ONLY (keep the aud model; do NOT
  add `allowedClients` — see A1 note on AND semantics). The agent's M2M token is made to
  carry this same `aud` by the V3 Lambda (Set B).
- [ ] **A6. Repoint SSM `/coa/*`** (`issuer`, `userpool-id`, `userpool-client-id`,
  `cognito-domain-prefix`) → Loom values, AND redeploy the containers that read
  `JWT_ISSUER_URL`/`JWT_CLIENT_ID` (the serve + MCP container env) so the **second,
  in-container** JWT check also trusts Loom.
- [ ] **A7. Repoint the control-plane API authorizer** → Loom. NOTE (from A0): this is a
  **custom Lambda authorizer** `coa-dev-authorizer` (API GW `TokenAuthorizer` id `ndiz9o`),
  NOT a native Cognito authorizer. Repoint = change **that Lambda's** issuer/pool config
  (its env / the `/coa/*` SSM it reads) to Loom, not an API Gateway authorizer setting.
- [ ] **A8. Rebuild the group→role mapping in Loom terms.** COA resolves roles from
  `cognito:groups`. Create/confirm the Loom groups and add grants in `coa-dev-roles` so
  Loom groups map to COA roles (migrate the `Admin`→`platform-admin` seed to a Loom
  group; add an approver group → `data-steward` on `pharma`). **[verify]** the roles
  table key shape by reading one row.
- [ ] **A9. Verify end-to-end as a HUMAN Loom user.** Log into the COA web app with a
  Loom user; confirm namespaces/panels render (not scope-gated empty). Confirm a
  control-plane call and an MCP `query` both authorize under the Loom token. This proves
  all 5 bindings moved together (a half-migrated state fails here).
- [ ] **A10. Mark `coa-dev-user-pool` retireable.** Once A9 passes, document that the COA
  pool is no longer the trust anchor; schedule its teardown (do NOT delete until the full
  demo is green, as a rollback anchor).

**A exit criteria:** a Loom human user fully operates COA (web + MCP + control-plane);
no COA surface still trusts `coa-dev-user-pool`.

---

## TASK SET B — ~~V3 pre-token Lambda for the Beacon agent~~ **DROPPED (2026-10-21)**

> **DROPPED.** Verified that a Cognito pre-token Lambda cannot inject `aud` or
> `cognito:groups` onto an M2M token (machine identity), so COA cannot authorize a Beacon
> M2M token without COA code changes we are not making. Beacon reads DIRECT instead (see
> the decision block at top + Set D). Kept below for the record only.

> Goal: the **Beacon agent** obtains a **Loom-issued M2M token** that COA accepts via its
> **existing `allowedAudience` (aud) authorizer** — because the V3 pre-token Lambda shapes
> the M2M token to carry (a) a valid `aud` = the Loom client COA trusts, and (b)
> `cognito:groups` so COA's role resolver grants it `data-analyst`. **COA is NOT changed**
> (no `allowedClients`); the token is made to look like a valid ID token to COA.
> Depends on Set A (COA trusts Loom).

- [ ] **B1. Beacon agent M2M client in Loom.** REUSE the existing
  `loom-user-pool-m2m-client` (`752qo4e554h96dj9352p930llb`, client_credentials, has
  secret) — or add a dedicated one. Add scope `loom/mcp:read` (currently only
  `loom/invoke`). Secret stays in Secrets Manager (never in code/chat).
- [ ] **B2. Write the `V3_0` pre-token-generation Lambda** on the Loom pool. On a
  client-credentials token **for the Beacon agent client only**, inject into the ACCESS
  token:
  - **`aud` = the Loom web client id COA trusts** (`3o0q6hdtfo1sphqq7jtqaspb3f`, i.e. the
    value in COA's `allowedAudience` from A5) — this is what makes COA's aud authorizer
    accept the M2M token like an ID token.
  - **`cognito:groups = ["beacon-agents"]`** + an `email`/identifier — so COA's grant
    resolver maps it to `data-analyst`.
  Leave ALL human tokens untouched.
  **🔴 [verify] — the hinge:** that a `V3_0` Lambda can **set/override the `aud` claim**
  on an M2M access token (some claims are reserved/non-overridable). If `aud` is NOT
  injectable: fall back to (i) the **gateway** presenting a token COA accepts, or (ii)
  authorize COA on a shared **scope** both token types carry (needs COA to validate the
  access token, not the ID token). Decide only if the verify fails.
- [ ] **B3. Attach the Lambda to the Loom pool** `LambdaConfig.PreTokenGenerationConfig`
  with `LambdaVersion=V3_0` (Loom is ESSENTIALS — supported). Grant Cognito invoke perms.
- [ ] **B4. Grant the Beacon agent group read in COA.** `CreateGrant` / `coa-dev-roles`:
  Loom group `beacon-agents` → **`data-analyst`** on `pharma` (read-only; NO
  ontology-write — the agent must not write).
- [ ] **B5. Confirm COA accepts the shaped token UNCHANGED.** COA's MCP authorizer stays
  `allowedAudience=[3o0q6…]` (A5). The agent token now carries that `aud` (B2), so it
  passes COA's existing check with **no COA-side authorizer change**. **[verify]** the
  in-container `claims.py` check (`JWT_CLIENT_ID`/`aud`) also accepts it.
- [ ] **B6. Smoke-test the agent token path (no Beacon code yet).** Script: mint a
  client-credentials token for the Beacon client at Loom's token endpoint (V3 Lambda
  shapes it) → call COA MCP `query`/`graph_traverse` through the gateway → confirm it
  authorizes as `data-analyst` and returns data (not fail-closed). Proves B1–B5 before any
  Beacon wiring.

**B exit criteria:** a Loom-issued Beacon **M2M** token, shaped by the V3 Lambda to carry
a valid `aud` + `beacon-agents` group, is accepted by COA's **unchanged** `allowedAudience`
authorizer and authorized read-only on `pharma` — verified by a direct smoke call.

---

## TASK SET C — Register COA MCP on the governance gateway (design §1a)

> Goal: COA's MCP server becomes a governed target on the Enterprise MCP Governance
> Gateway, so Beacon reaches it through one governed endpoint (Cedar authz + audit).
> Depends on A (COA trusts Loom) so the gateway can forward a Loom token COA accepts.

- [ ] **C1. Read the Atlassian connector** in the gateway repo to copy the
  AgentCore-**Runtime** target pattern (COA MCP is a Runtime ARN, not a static https URL).
  **[verify]** the exact Runtime-MCP URL template (url-encoded ARN).
- [ ] **C2. New stack `cdk/connectors/coa_stack.py`** modeled on it: read gateway
  `id`/`arn`/`policy-engine/id` from SSM; `CfnGatewayTarget` named `CoaContext` pointing
  at COA MCP runtime's invocation endpoint; `listing_mode="DEFAULT"`.
- [ ] **C3. Outbound credential:** configure the target to forward the caller's Loom
  token (pass-through) so COA authorizes the acting principal. **[verify]** the gateway
  target supports token pass-through vs. requiring its own credential provider.
- [ ] **C4. Cedar permits:** one `CfnPolicy` per tool Beacon uses —
  `CoaContext___query`, `CoaContext___graph_traverse`, `CoaContext___translate` —
  `mode=ENFORCE` (no permit ⇒ denied + hidden from tools/list).
- [ ] **C5. Deploy + verify:** `tools/list` through the gateway shows `CoaContext___*`;
  a `tools/call` succeeds under a Loom token.

**C exit:** COA MCP read tools are callable through the governance gateway under a Loom
identity, Cedar-permitted and audited.

---

## TASK SET D — Beacon read adapter (`context_graph.py`)

> Goal: Beacon's reserved `BEACON_GRAPH_BACKEND="context"` reads COA through the gateway
> using the Beacon agent M2M token (Set B). Engine stays unchanged.

- [ ] **D1. Read `engine/graph/__init__.py` + `local_graph.py`/`neptune_graph.py`** to
  learn the exact backend interface (**[verify]** method surface before coding).
- [ ] **D2. Implement `engine/graph/context_graph.py`** as an MCP client of the gateway:
  mint/refresh Beacon's Loom M2M token, open a Streamable-HTTP MCP session, map engine
  graph calls → `CoaContext___query`/`CoaContext___graph_traverse`, cache returned class
  IRIs for the commit step.
- [ ] **D3. Return engine DTOs** identical to `neptune_graph` so `signals.py` is untouched.
- [ ] **D4. Unit tests** against a mocked COA `query` response (no live COA needed).
- [ ] **D5. Live test:** given the §4 signal, adapter returns the grounded blast radius
  from COA.

**D exit:** Beacon grounds a signal from COA via the gateway, read-only, as the agent.

---

## TASK SET E — Workflow DB + score→PENDING write path

> Goal: the transient Beacon workflow store (retired with AWS Context eventually).

- [ ] **E1. CDK:** add DynamoDB `beacon-workflow` (PK `factId`; GSI `status-index`;
  on-demand; PITR). **[verify]** which Beacon CDK stack should own it (`memory_stack.py`
  is the closest analog).
- [ ] **E2. Row schema:** `{factId, text, score, affectedEntityIRIs[], status, approver,
  note, reviewDate, committedOntologyId, ingestJobId, createdAt, updatedAt}`.
- [ ] **E3. Write path:** on a Memory-triggered invocation, Beacon scores via
  `engine/signals.py` and writes a `PENDING` row with the IRIs captured in D.
  **[verify]** whether scoring runs in `beacon_trigger.py` or the runtime.

**E exit:** one `PENDING` row per signal, carrying score + affected IRIs.

---

## TASK SET F — Review screen → commit (Priya writes, with HER token)

> Goal: the approval writes the fact to COA — under **Priya's own live Loom token**,
> `data-steward` grant. Beacon agent token is NOT used here.

- [ ] **F1. Review screen** lists `status=PENDING` via `status-index` with the
  COA-grounded reason; Priya logs in via Loom.
- [ ] **F2. Approve action:** set row `APPROVED` + `approver`/`note`/computed
  `reviewDate` (from current date, never hardcoded).
- [ ] **F3. Commit on approval (control-plane Upload route, NOT MCP):** render the
  approved fact as Turtle against the cached COA class IRIs + provenance
  (`approvedBy`/`reviewDate`/`source=Beacon`) → `GetOntologyUploadUrl` → PUT →
  `IngestOntologyFromS3(ontologyType="user_created", ontologyId="calanthir-approved-facts")`
  → poll `GetIngestStatus` → set row `COMMITTED` + ids. **All calls carry Priya's live
  Loom token** (pass-through). **[verify]** the review screen can forward her token.
- [ ] **F4. Provenance vocabulary:** match COA's uploaded-ontology prefixes (read a
  sample induced ontology via `DownloadOntology` after G).

**F exit:** Priya's approval commits the fact to COA, audited as Priya, idempotent on
retry; agent never holds write authority.

---

## TASK SET G — Phase 0 seed the enterprise graph (pharma)

> Goal: COA holds the Calanthir pharma ontology for Beacon to ground against.

- [ ] **G1. Generate fictional CSVs** (`gen_data.py`): suppliers, materials, batches,
  drugs, trials, trial_sites, regions — with real PK/FK columns so induction infers the
  Supplier→Material→Batch→Drug→Trial→Site→Region chain. Fixtures: Kingsport API Labs,
  XR-7, lot B-2291, Nuvora, NUV-301, Ireland/Germany/US.
- [ ] **G2. S3 + Glue:** create bucket (tag `coa:namespace=<pharma-ns>`); **hand the
  `aws s3` upload to the user's terminal** (blocked from agent session); create Glue DB +
  Athena-queryable tables over the CSVs.
- [ ] **G3. Namespace + source:** `CreateNamespace pharma`; register a **Glue Data
  Catalog** source → scan → review.
- [ ] **G4. Induce + accept:** `StartInduction` → review proposal → `AcceptProposal`.
- [ ] **G5. Verify:** a graph query returns the Kingsport→…→NUV-301→sites chain.

**G exit:** the pharma enterprise graph exists in COA with the §3 ontology.

---

## TASK SET H — Verify the loop closed (end-to-end)

- [ ] **H1. End-to-end:** inject the §4 signal → Beacon grounds (D) → scores → `PENDING`
  (E) → Priya approves (F) → committed to COA → re-query COA for the fact.
- [ ] **H2. Assert provenance:** the committed fact is present with `coa:approvedBy`
  (Priya), `coa:reviewDate`, `coa:source=Beacon`; demo before/after.
- [ ] **H3. Clean up** any scratch; document teardown (`make destroy-dev` for COA,
  the Beacon stacks, and the retired `coa-dev-user-pool`).

**H exit:** a signal becomes governed, provenance-stamped enterprise knowledge through
the full cycle — the demo's thesis, proven on the real system.

> Reminder: the **write** (Set F) uses Priya's human Loom token, NOT the Beacon agent
> token from Set B. The agent reads; the human approval writes.
