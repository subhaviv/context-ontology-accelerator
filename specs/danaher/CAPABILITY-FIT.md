# Danaher / IDBS COA Evaluation — Capability Fit

> Customer: **Danaher (IDBS)**. Scope: the IDBS *COA Evaluation Requirements (DRAFT)*
> — ontology **discovery** + **OWL mapping/alignment** for data-migration investigation.
> This is a *different emphasis* from the Calanthir namespace (runtime semantic graph +
> deterministic metric scoring). This doc maps each eval requirement to what the COA
> engine in this repo **actually does today**, grounded in code/docs, not guessed.
>
> Sources read: `external-docs/content/ontologies.md`, `packages/ontology-engine/README.md`.
> Written 2026-10-23.

## Headline

The eval's core asks — **infer an ontology, emit it as OWL, do it both independently and
target-informed, then produce confidence+rationale mappings for human review** — map
*directly* onto capabilities the COA ontology-engine already has. This is a much better
fit than the Calanthir work suggested, because Calanthir only exercised the
induce→serve→metric path, not the induce→ground→propose→OWL path that this eval is about.

The grounding + proposal workflow **is** the target-informed-discovery + candidate-mapping
machine the eval describes. It was built for exactly this shape of problem.

## Requirement → capability map

### Ontology Discovery (FR-OD-*)

| Req | COA capability today | Fit |
|---|---|---|
| FR-OD-01 infer ontology from a dataset | `StartInduction` over data-catalog datasources; two strategies: `table_to_ontology` (embedding match, one OWL class/table, FKs→object props) and `rigor_ontology` (RIGOR generate-LLM + judge-LLM, arXiv 2506.01232). | ✅ built |
| FR-OD-02 known-ontology workflow dataset | Need a Danaher dataset where IDBS already knows the expected ontology (benchmark case). Engine demo ships an OMG P&C insurance benchmark (`demo/pc-insurance-ddl.sql`, 13 tables) as a worked example of exactly this. | ✅ supported; needs Danaher data |
| FR-OD-03 unfamiliar/messy dataset | Same induction path; no prior-knowledge assumption. | ✅ supported; needs Danaher data |
| **FR-OD-04 independent discovery (no target)** | `groundingMode=NONE` → "keeps an induced ontology fully independent." Exactly the independent-discovery mode. | ✅ built |
| **FR-OD-05 target-informed discovery (with target)** | **Grounding.** Load the prescribed target ontology as a *reference/foundational* ontology, pass its id in `groundingOntologyIds`, run with `groundingMode=ENHANCED` (LLM reranker verifies domain alignment). Induction aligns induced classes to the target via `rdfs:subClassOf`/`skos:*Match`. | ✅ built — this IS the feature |
| FR-OD-06 compare independent vs target-informed | Two separate induction runs (NONE vs ENHANCED+target); each produces its own proposal/ontology artifact → compare. No *built-in* diff report (manual/scripted comparison of the two OWL artifacts). | ⚠️ artifacts yes, comparison report is manual |
| FR-OD-07 rationale / confidence on inferred elements | `ENHANCED` reranker returns a per-class JSON verdict; grounding candidates carry match info; proposals are reviewable. RIGOR judge-LLM refines per fragment. | ✅ confidence+rationale present |
| FR-OD-08 traceability to source | Classes minted from table IRIs; FK notes; `GET /graph/class` carries provenance incl. `superseded` history; R2RML ties class→table. | ✅ built |
| FR-OD-09 evaluate inferred vs expected ontology | Both are OWL/Turtle → comparable. Validation suite (HermiT, OntoQA, OoPS!) scores quality. No built-in "score against expected" harness. | ⚠️ artifacts comparable; scoring is manual/scripted |
| FR-OD-10 compare vs industry-standard ontology | Grounding targets ship FIBO, Schema.org, Dublin Core, PROV-O, FOAF as foundational ontologies — ground against the standard directly. | ✅ built |

### Mapping & Alignment (FR-OM-*)

| Req | COA capability today | Fit |
|---|---|---|
| FR-OM-01 compare inferred vs target, correspondence/difference | Grounding produces `skos:*Match`/`subClassOf` links between induced classes and the target. The proposal **Grounding tab** / `matchesUrl` artifact lists candidates per source table. | ✅ built |
| FR-OM-02 candidate mappings where correspondence exists | The `matches` artifact: `source_table_identity` → `candidates[].entity_uri`. Literally "candidate mappings." | ✅ built |
| FR-OM-03 confidence on each mapping | `ENHANCED` reranker verdict is the confidence signal; STANDARD = exact-name only (binary). *Whether a numeric confidence score is exposed per candidate needs a live check of the match artifact.* | ✅ mechanism exists — [verify numeric score field] |
| FR-OM-04 rationale/evidence for a reviewer | ENHANCED reranker "verifies domain alignment," rejects same-name-different-meaning (Dublin Core "Policy" ≠ insurance "policy"). RIGOR judge refines. Reviewer sees candidates + source table identity. *Depth of per-mapping rationale text needs a live check.* | ✅ mechanism exists — [verify rationale text surfaced] |
| FR-OM-05 inferred concepts with NO target mapping | "Mark novel (no grounding)" / `novelTables`; novelty check in both strategies separates matched from novel. | ✅ built |
| FR-OM-06 target concepts with NO inferred match | Not obviously surfaced — grounding runs induced→target; the reverse (target classes never matched) is a set-difference over the target ontology. Likely a scripted post-step, not a built-in report. | ⚠️ likely manual |
| FR-OM-07 human-in-the-loop, mappings not auto-approved | The entire **proposal review** workflow: proposals are pending until a human accepts; grounding overrides are staged until "Save changes"/`UpdateProposal`. Nothing is applied without human accept. | ✅ built — core design |
| FR-OM-08 assess mapping quality vs expert mappings | No built-in. Export mappings → IDBS specialists rate (matches the eval's own "Evaluation Method"). | ⚠️ manual (expected by eval) |

### Data Input (FR-DI-*)

| Req | COA capability today | Fit |
|---|---|---|
| FR-DI-01 bounded representative datasets | Namespace + approved sources model. | ✅ |
| **FR-DI-02 structured + semi-structured + unstructured** (Advanced Spreadsheet JSON, Word/Excel, instrument) | Structured: relational induction. Unstructured: **`unstructured_lexical_graph` / document induction** (`/induce/unstructured/`) builds OWL from a lexical knowledge graph produced by the doc-ingestion pipeline. **BUT v0 has real gaps** (see Risks). Semi-structured spreadsheet-JSON / Word / Excel / instrument ingestion path into that pipeline needs verification — `packages/sources/documents/` is the place to check. | ⚠️ partial — structured ✅, unstructured v0 with gaps, spreadsheet/instrument [verify] |
| FR-DI-03 messy/incomplete data | Induction is tolerant (LLM-driven); no schema-cleanliness precondition. | ✅ plausible; prove on messy Danaher data |

### Outputs (FR-OUT-*)

| Req | COA capability today | Fit |
|---|---|---|
| **FR-OUT-01 inferred ontologies in OWL** | COA stores ontologies in **OWL/Turtle**; engine is literally "automated induction of **OWL 2** ontologies." Proposal Turtle is downloadable; upload/ingest supports Turtle/.ttl, RDF/XML, OWL/XML, JSON-LD. | ✅ built — native format |
| FR-OUT-02 independent & target-informed retained separately | Two induction runs = two proposals/ontologies = two artifacts. | ✅ |
| FR-OUT-03 mapping artifact w/ confidence+rationale | The `matches` artifact (`matchesUrl`). | ✅ (depth per FR-OM-03/04) |

### Non-Functional (NFR-*)

| Req | COA capability today | Fit |
|---|---|---|
| NFR-01 data security/handling | Platform governance (RRM/Cedar grants, namespace isolation). | ✅ |
| NFR-02 traceability of each run | Jobs are tracked (DynamoDB job store, status stages, `InductionReport.warnings`); proposals retained. | ✅ |
| **NFR-03 repeatability across runs** | **The hard one.** Induction is LLM-driven (Claude generator + judge, reranker) → inherent run-to-run variance. `STANDARD`/`NONE` modes are deterministic-ish (exact-name / no LLM); `ENHANCED`/`rigor_ontology` are not. This is a graded eval criterion (DO-07) and a genuine risk to call out. | ⚠️ RISK — quantify variance; consider STANDARD for the repeatability run |

## Top risks / gaps to flag to Danaher up front

1. **NFR-03 repeatability vs LLM non-determinism.** ENHANCED grounding and RIGOR
   induction call LLMs; identical inputs can yield different ontologies. Plan the
   repeatability assessment deliberately (fixed model version, possibly STANDARD mode for
   the determinism run, report observed variance). Do **not** promise bit-identical output.

2. **Unstructured induction is v0 with documented gaps** (`ontology-engine/README.md`):
   sparse `owl:DatatypeProperty` output, no cross-run normalizer persistence, no hierarchy
   axioms (entailment is pass-through), no SHACL/HermiT validation on doc-induced proposals.
   If the Danaher dataset leans heavily on Word/instrument/unstructured, set expectations.

3. **FR-DI-02 spreadsheet-JSON / Excel / instrument ingestion** — the eval explicitly names
   "Advanced Spreadsheet data extracted as JSON." Need to verify what `packages/sources/`
   actually ingests beyond relational + generic documents. **[verify against sources code]**

4. **Reverse-direction gaps (FR-OM-06) and comparison reports (FR-OD-06/09, FR-OM-08)** are
   not built-in. These are scripted post-processing / expert-rating steps — matches the
   eval's own "Evaluation Method" (specialists rate using generated artefacts), so acceptable,
   but we own writing the comparison/diff tooling.

5. **Tier-2 answerability backend caveat** (`na_only` no-ops `coa:isMapped`) — not central to
   a *discovery/mapping* eval (that's a querying concern), but note it if any query demo is
   added. Use `opensearch_neptune` if structured query is in scope.

## What this means for the eval plan (spec-driven)

The natural shape, per the eval's four moves:
1. **Independent discovery** — induct Danaher dataset with `groundingMode=NONE` → OWL artifact (DO-01).
2. **Target-informed discovery** — load the prescribed target ontology as a reference, re-induct with `groundingMode=ENHANCED` + `groundingOntologyIds=[target]` → OWL artifact (DO-02).
3. **Compare** the two OWL artifacts (DO-03) — scripted diff we write.
4. **Mapping artifact** — the grounding `matches` artifact → candidate mappings w/ confidence+rationale (DO-04/05); novel + unmatched-target as set operations.
Then repeatability runs (DO-07) and specialist rating against EC-01…EC-08.

**[verify] items** (need a live COA / code read before locking a design):
- numeric confidence field on each mapping candidate (FR-OM-03)
- per-mapping rationale text surfaced to reviewer (FR-OM-04)
- spreadsheet-JSON / Excel / instrument ingestion in `packages/sources/` (FR-DI-02)
- whether any built-in independent-vs-target or inferred-vs-expected diff exists (FR-OD-06/09)

## Open questions for Subhaviv / Danaher
- What is the **prescribed target ontology** for the target-informed run? (A standard like
  FIBO/SIO/an OBO ontology? A Danaher/IDBS-internal one we'd upload as a reference?)
- What **datasets** will Danaher provide, and how much is unstructured/instrument vs relational?
- Is this eval **demo-only** (we drive COA, produce artifacts) or do we also stand up a
  dedicated **danaher namespace** on the deployed COA (mirroring calanthir-pharma)?
