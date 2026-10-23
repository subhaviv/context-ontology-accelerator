# COA Handoff — Context Ontology Accelerator (Calanthir namespace)

> Scope: **COA platform + Calanthir pharma namespace only.** No Beacon agent internals.
> Written 2026-10-23. Repo: `context-ontology-accelerator`. Branch: `feat/beacon-governed-read`.
> Purpose: let a fresh session (or another person) pick up the COA-side work without replaying chat.

---

## 1. What COA is (one paragraph)

The Context Ontology Accelerator is IDBS's platform for deriving a **governed semantic
layer** over customer data: it infers/holds an ontology, exposes the underlying classes
and relationships, and serves **deterministic metrics** computed in SQL (Athena/Glue) over
that layer. It is served to agents through an MCP surface. In this repo it is a monorepo
(`packages/*`) deployed by CDK (`infra/`, `coa-dev-*` stacks). The **Calanthir
pharmaceuticals** namespace (`specs/calanthir-pharmaceuticals/`) is our demo tenant.

There is also a separate, newer **evaluation requirements** doc from IDBS framing COA as an
*ontology-discovery + OWL-mapping* tool for data migration (independent vs target-informed
discovery, candidate mappings with confidence/rationale, human-in-the-loop). That is a
*different emphasis* from how we use COA here (runtime semantic graph + deterministic
metric scoring). Keep both framings in mind; they share the determinism / confidence /
traceability themes.

## 2. Repo map (COA platform)

- `packages/ontology-engine/` — ontology inference/holding.
- `packages/metric-service/` — **the metrics engine** (OSI metrics, Tier-1 deterministic SQL). See its `README.md` and `examples/`.
- `packages/mcp-server/` + `packages/mcp-proxy/` — the MCP surface agents call.
- `packages/vkg/` — virtual knowledge graph / translate layer (`translate-server.py`).
- `packages/data-layer/`, `packages/control-plane/`, `packages/sources/` — storage, control plane, source connectors.
- `packages/web-app/` — the COA web UI (includes the **Metrics → Import OSI** flow).
- `infra/` — CDK; stacks are `coa-dev-*` (namespace, metric-service, ontology, vkg, mcp, agent-mcp, serve, web, auth, …). `infra/bin/app.ts` wires them.
- `external-docs/content/` — authoritative platform docs: `metrics.md`, `ontologies.md`, `namespaces.md`, `sources.md`, `serve.md`, `agent-access.md`, `deploying.md`.
- `specs/calanthir-pharmaceuticals/` — our demo namespace: `design.md`, `requirements.md`, `tasks.md`, `seed/`.

## 3. Calanthir pharma namespace — current state

**Seeded graph (deployed).** The Calanthir pharma namespace holds these entity classes
(all reachable via the MCP query surface):
- `materials` (e.g. `MAT-001` = XR-7 Active Pharmaceutical Ingredient; `MAT-003` = Release Assay Reagent Kit)
- `suppliers` (e.g. Meridian)
- `batches`, `trials`, `trial_sites` (5 sites: MD Anderson, Royal Marsden, NCC Tokyo, Mayo Clinic, Charité Berlin — **no "Boston" site**)
- `kpis` (e.g. `KPI-004`, carries a `tier`)
- `measured_by` — the junction class linking an entity to the KPI(s) that measure it. Its FK columns (`material_id`, `supplier_id`, `batch_id`, `trial_id`, `site_id`, `kpi_id`) are **only readable by querying its rows (data), NOT via `describe_schema`** — see §5 blocker.

**Namespace / wrapper facts** (from `design.md §1a`): a dedicated M2M MCP wrapper + serve-agent is BUILT + DEPLOYED for the governed read path. Namespace id, wrapper runtime ARN, and the M2M secret id are recorded in `design.md` (search `COA_NAMESPACE_ID`, `COA_AGENT_MCP_RUNTIME_ARN`, `COA_MCP_M2M_SECRET_ID`).

## 4. The scoring-in-COA migration (IN PROGRESS — this is the live workstream)

**Decision locked (`design.md §13`):** scores must be **deterministic** — computed by COA
metrics (SQL) or code, **never** emitted by an LLM via structured output. Reason: a score
has no closed-set guard, so an LLM-emitted score is non-deterministic / unauditable /
ungovernable. Structured output is right for *grounding* (shape + closed-set guard), wrong
for *scoring*.

**Target model (`design.md §11` + the migration section):** each scoring signal becomes
one **OSI metric returning weighted points**; the consumer just **sums** them.
`points = value × WEIGHT`; `score = Σ(points) × extraction_confidence`.
WEIGHTS (sum=100): `breaks_expected=25, kpi_tier=20, affected=20, urgency=15, novelty=10, source_authority=10`.

**Per-signal feasibility as Tier-1 COA metrics:**
| Signal | Metric-able? | Note |
|---|---|---|
| breaks_expected | ✅ | `clamp((actual-baseline)/baseline) × 25`, SQL over `materials`, dim `{material_id}`. |
| kpi_tier | ✅ | SQL over `kpis.tier` for the measured KPI. |
| source_authority | ✅ | SQL over person/role authority. |
| urgency | ⚠️ | needs a date/event column; doable but data-dependent. |
| novelty | ❌ (stays external) | stateful "seen before?" — not a COA fact. |
| affected | ❌ (hard) | multi-hop traversal material→KPI→sites→population; Tier-2 NL query returned 0 rows. |

**Done so far:**
- `design.md` §11/§12/§13 + migration section written.
- First metric authored: `specs/calanthir-pharmaceuticals/seed/calanthir-scoring-metrics.osi.yaml`
  — `score_breaks_expected`, SQL = `LEAST(1, GREATEST(0,(lead_time_days-baseline_lead_days)/baseline_lead_days)) * 25`, dim `{material_id}`.
  Expected for MAT-001: `(88-75)/75 × 25 = 4.33` weighted points.

**NEXT STEPS (resumable):**
1. **Import** `calanthir-scoring-metrics.osi.yaml` via the web UI **Metrics → Import OSI** (presigned-upload flow; cannot be driven headless — a human does this).
2. Verify it returns **4.33** at Tier-1 with a `{material_id: MAT-001}`-bound call.
3. Author the other metric-able signals (`score_kpi_tier`, `score_source_authority`, `score_urgency`) into the same OSI file with their baked weights.
4. Leave `novelty` + `affected` out of COA (flagged, computed by the consumer), per §13.

## 5. Known blockers / gotchas (COA-side)

- **`describe_schema` does not expose FK / object-property columns.** The junction's FK
  columns are discoverable ONLY by querying its **rows** (data), not via schema
  introspection and not via RDF object-property triples (those come back empty on this MCP
  surface). Any "self-describing FK map" from schema introspection is blocked until COA's
  `describe_schema` is fixed to emit object-properties with ranges. (This is why the
  consumer currently uses a hardcoded FK map, flagged blocked-on-COA.)
- **Tier-2 (NL→SQL) multi-hop traversal is unreliable** — the `affected` chain
  (material→KPI→trial_sites→population) returned 0 rows. Needs either a baked join in a
  metric or a better graph-traversal surface.
- **Metric import is UI-only** (presigned upload) — not scriptable from an agent session.
- Deploy notes: CDK uses `coa-dev-*` stacks with `cdk.json` context; follow
  `external-docs/content/deploying.md`. Mutating deploys are run by the user; agent runs
  read-only verification.

## 6. Git state (2026-10-23)

- Branch: `feat/beacon-governed-read`.
- Last COA commit: `a0c1671` (design: scoring deferred decision, resolver-is-the-sauce, probe OSI).
- **Uncommitted:** `design.md` (§13 + migration section), `seed/calanthir-scoring-metrics.osi.yaml`.
  Throwaway probe scripts `seed/_*.sh` are intentionally NOT to be committed. `pnpm-lock.yaml` churn is unrelated.

## 7. Authoritative references (read these, don't guess)

- `specs/calanthir-pharmaceuticals/design.md` — §11 scoring intent, §12 resolver, §13 determinism decision + migration.
- `packages/metric-service/README.md` + `external-docs/content/metrics.md` — how OSI metrics / Tier-1 work.
- `external-docs/content/ontologies.md`, `namespaces.md` — ontology + namespace model.
- IDBS "Context Ontology Accelerator — Evaluation Requirements (DRAFT)" — the ontology-discovery/OWL-mapping framing (uploaded PDF; not in repo).
