# Calanthir Pharmaceuticals — Beacon ⇄ COA Governance Cycle

**Spec status:** Draft v1 · 2026-10-21
**Owner:** Subhaviv
**Target account:** `agentops-publisher` (195698601974), `us-east-1`
**Vertical:** Pharmaceutical manufacturer (fictional company **Calanthir Pharmaceuticals**)

---

## 1. Purpose & Problem Statement

Demonstrate a **complete, closed governance loop** in which an autonomous agent (Beacon)
ingests a business signal, **reasons over a governed enterprise knowledge graph**
(the Context Ontology Accelerator / AWS Context), scores the signal's materiality,
routes it for **human approval**, and — on approval — **writes the approved fact back
into the enterprise graph** so every other agent inherits it.

The demo must make three things true and visible:

1. There is **one enterprise knowledge graph** — COA/AWS Context — not two. Beacon is a
   *governance workflow* over that graph, not a second graph.
2. Beacon's reasoning is **grounded in the governed graph** (blast-radius traversal),
   not in hand-built local state.
3. The loop **closes**: an approved fact becomes durable, provenance-stamped enterprise
   knowledge committed through a governed write path.

### Non-goals
- Not a drug-discovery or clinical-decision tool. No medical/clinical advice is produced.
- Beacon does **not** become a second knowledge graph.
- No user identity is forwarded to the inference/serve hop (correlation only — see §7).

---

## 2. Fictional World (fictional data only)

**Company:** Calanthir Pharmaceuticals (coined name; no real-world namesake — chosen
over eMed and over colliding names Meridian/Halcyon/Noventra on 2026-10-21).

**Canonical fixtures (all fictional):**
| Entity | Value |
|--------|-------|
| Supplier | Kingsport API Labs |
| Material (API) | compound XR-7 |
| Batch | lot B-2291 |
| Drug (product) | Nuvora |
| Trial | NUV-301 (Phase 3) |
| Trial sites | Ireland, Germany, United States |
| Approver persona | QA & Regulatory Affairs lead (e.g. "Dr. Elena Vasquez") — **not** a clinician |

All demo dates are computed from the current date; no hardcoded calendar strings.

---

## 3. Ontology (induced in COA from Glue-over-S3)

**Classes:** `Supplier`, `Material`, `Batch`, `Drug`, `Trial`, `TrialSite`, `Region`.

**Relationships (blast-radius chain):**
```
Supplier --SUPPLIES--> Material --USED_IN--> Batch --PRODUCES--> Drug
Drug --DOSED_IN--> Trial --RUNS_AT--> TrialSite --IN--> Region
```

This chain is the reasoning spine: a supplier delay traverses
`Supplier → Material → Batch → Drug → Trial → TrialSite` to compute impact.

---

## 4. The Canonical Signal & Expected Reasoning

- **Input signal:** "Supplier Kingsport API Labs is ~3 weeks late on compound XR-7
  (the API for batch lot B-2291)."
- **Beacon grounds it over COA:** traverses the chain → "XR-7 feeds Batch B-2291 →
  Drug Nuvora → dosed in Trial NUV-301 → 4 sites, 2 in the EU, ~120 enrolled patients
  mid-titration."
- **Materiality:** HIGH (trial-blocking, patient-facing, EU regulatory exposure).
- **Routing:** to the QA/Regulatory approver.
- **Approved fact (committed to COA):** "XR-7 supply delay impacts NUV-301 continuity",
  stamped with `approvedBy`, `reviewDate`, `source=Beacon`.

---

## 5. Architecture — Two Planes, One Enterprise Graph

| Plane | Owner | Mutability | Store |
|-------|-------|-----------|-------|
| World-model: suppliers, materials, batches, drugs, trials, sites, relationships | **COA (the enterprise graph)** | Induced / curated | Neptune (COA) |
| Mapped instances (R2RML over source CSVs) | **COA** | Re-induced on source change | Neptune (COA) |
| **Signal intake + materiality score + PENDING queue** | **Beacon** | Live per-signal | Beacon workflow DB (DynamoDB) |
| **Approval decision + note + review date** | **Beacon** | Live per-approval | Beacon workflow DB |
| **Approved fact as durable enterprise knowledge** | **COA** (committed by Beacon) | Append-only | COA ontology `calanthir-approved-facts` |

**Rationale (verified against the COA API):** COA has **no `CreateProposal`** entry point
— proposals come only from `StartInduction` over a source — so COA cannot host the
*signal-intake* approval queue. That queue is therefore legitimately Beacon's, and is
exactly the transient workflow state that AWS Context's managed human-in-the-loop
curation would absorb in production. The *commit* to the enterprise graph uses COA's
governed **Upload route** (see §6).

---

## 6. Commit Path — Upload Route (locked decision)

On approval, Beacon commits the approved fact to COA via the **Upload route** (chosen
2026-10-21 over the heavier `StartInduction → UpdateProposal → ValidateProposal →
AcceptProposal` proposal rail):

1. **Render Turtle** — assert the approved fact against the **existing COA class IRIs**
   read during reasoning (§4), plus provenance literals
   (`coa:approvedBy`, `coa:reviewDate`, `coa:source`).
2. `GetOntologyUploadUrl(filename, ontologyId="calanthir-approved-facts", title=…)`
   → returns `uploadUrl` + `s3Key` (registers the `ontologyId`; no separate CreateOntology).
3. **PUT** the Turtle bytes to `uploadUrl`.
4. `IngestOntologyFromS3(ontologyId, s3Key, format="turtle", ontologyType="user_created")`
   → `202` + `jobId`.
5. Poll `GetIngestStatus` until `COMPLETED`
   (`QUEUED → RUNNING → EMBEDDINGS_SYNC → COMPLETED`); record `ontologyId`+`jobId` on
   the workflow row; flip status to `COMMITTED`.

**Committed facts land in a dedicated `calanthir-approved-facts` ontology** so curated
facts never clobber the induced world-model (merge is by IRI only where it references
shared classes).

### Accepted commit-path constraints (not glossed)
- Commit granularity is **ontology-fragment + async ingest** (seconds + a status poll),
  **not** a synchronous single-triple insert.
- Committed facts are **graph-visible but `is_mapped=false`** — answerable via graph
  traversal / semantic search, **not** via the R2RML-backed NL→SQL Tier-2 path.
  `is_mapped` is server-derived at ingest and cannot be forced by upload. This is correct
  for curated knowledge.
- The Upload route **bypasses COA's proposal/validate/accept review**; the governance
  checkpoint in this design is **the approver's sign-off in Beacon** (§4), not a second
  COA-side review.

---

## 7. Identity (locked — two-plane model)

- **User → Loom → runtime invoke:** where human identity lives (not exercised by the
  COA serve/commit hop).
- **Agent (Beacon) → M2M token → COA serve/MCP:** carries **correlation only**
  (`session.id`), not identity. No OBO, no user-token forwarding to COA's inference/serve
  surface.

---

## 8. Requirements & Acceptance Criteria (phased, each independently demoable)

### Phase 0 — Seed the enterprise graph
- **R0.1** Generate fictional pharma CSVs (suppliers, materials, batches, drugs, trials,
  sites, regions) consistent with §2–§3.
- **R0.2** Stage CSVs in an S3 bucket in `agentops-publisher`, tagged
  `coa:namespace=<pharma-ns-id>`.
- **R0.3** Create COA namespace `pharma`; register a **Glue Data Catalog** source over
  the CSVs.
- **R0.4** Run `StartInduction`; review; `AcceptProposal`.
- **AC0:** COA holds classes `Supplier/Material/Batch/Drug/Trial/TrialSite/Region` with
  the §3 relationships and real IRIs; a graph query returns the Kingsport→…→NUV-301 chain.

### Phase 1 — Beacon reads COA (reasoning)
- **R1.1** Implement Beacon's `BEACON_GRAPH_BACKEND="context"` as a **read adapter** to
  COA's serve MCP runtime (`query`, `graph_traverse`, `translate`), authed with an M2M
  token (§7).
- **R1.2** On the §4 signal, Beacon grounds the blast radius from COA and captures the
  referenced class IRIs for later commit.
- **AC1:** Given the signal, Beacon returns the grounded impact statement (sites,
  EU count, patient count) sourced from COA, with no hand-built local graph.

### Phase 2 — Score + queue (Beacon workflow DB)
- **R2.1** Create DynamoDB table `beacon-workflow` (agentops-publisher). Row:
  `{factId, text, score, affectedEntityIRIs[], status∈{PENDING,APPROVED,REJECTED,COMMITTED},
  approver, note, reviewDate, committedOntologyId, ingestJobId}`.
- **R2.2** Beacon writes the scored candidate as `PENDING`.
- **AC2:** The signal produces exactly one `PENDING` row with a materiality score and the
  affected IRIs.

### Phase 3 — Approval (QA/Regulatory, in Beacon)
- **R3.1** Review screen lists `PENDING` rows with COA-grounded reason text.
- **R3.2** Approver adds a note; Approve flips the row to `APPROVED` with `approver` +
  computed `reviewDate`. No COA call at this step.
- **AC3:** Approving a row persists approver + note + review date and transitions it to
  `APPROVED`; rejecting transitions to `REJECTED`.

### Phase 4 — Commit to COA (Upload route)
- **R4.1** Implement the §6 Upload-route commit client (render Turtle → GetOntologyUploadUrl
  → PUT → IngestOntologyFromS3 → poll GetIngestStatus).
- **R4.2** Store `ontologyId`+`jobId` on the row; flip to `COMMITTED` on `COMPLETED`.
- **AC4:** On approval, the approved fact is ingested into `calanthir-approved-facts`;
  `GetIngestStatus` reaches `COMPLETED`; the row shows `COMMITTED` with the ids.

### Phase 5 — Verify the loop closed
- **R5.1** Re-query COA (`graph_traverse`/`GraphSearch`) for the approved fact.
- **AC5:** The approved fact is present in the enterprise graph with Priya-equivalent
  provenance (`coa:approvedBy`, `coa:reviewDate`, `coa:source=Beacon`), demonstrable as a
  before/after.

---

## 9. Build Order & Touch List

**Order:** Phase 0 → 1 → 2 → 3 → 4 → 5. Phases 2–3 (Beacon-local) may proceed in
parallel with Phase 0 if useful.

**Will touch:**
- New S3 bucket + fictional pharma CSVs + Glue table (Phase 0), `agentops-publisher`.
- New DynamoDB table `beacon-workflow` (Phase 2).
- Beacon repo (`agentops-beacon`): `context` read adapter (Phase 1), Turtle-render +
  Upload-route commit client (Phase 4), review-screen → commit wiring (Phases 3–4).

**Will NOT touch:**
- The deployed COA stacks — the demo only **calls** COA's APIs.
- Beacon's domain-agnostic engine internals (generic `entity`/`entity_id`), per the
  standing rule to keep the engine domain-agnostic.

---

## 10. Portability Note

Because COA induces the ontology from data and Beacon's engine is domain-agnostic, this
same cycle transfers to another vertical by swapping the CSVs + namespace and re-inducing
— no engine rewrite. The pharma build is therefore also the portability proof.

---

## 11. Open Decisions (to confirm during build)

- Approver display name (default "Dr. Elena Vasquez, QA & Regulatory Affairs").
- Whether to show the heavier proposal/validate/accept rail as a secondary "production
  curation gate" beat, or keep only the Upload route.
- Materiality scoring formula reuse from Beacon's existing `signals.py` spine.
