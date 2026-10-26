# Danaher / IDBS COA Eval — Eval Surface → Requirements Mapping

> Consolidates what we verified in code + live AWS: the **deterministic scorers**, the
> **induce input/output capture**, and the **live CloudWatch metrics** — mapped back to the
> IDBS requirement ids (FR-OD / FR-OM / FR-OUT / NFR / EC / DO). Written 2026-10-23.
> Supersedes the ⚠️/`[verify]` guesses in `CAPABILITY-FIT.md` where noted "CONFIRMED".
>
> **Langfuse: dropped.** **AgentCore Evaluations: not on the induce path** — induction is a
> Fargate + Bedrock `converse()` batch job (`ontology-stack.ts`, `inducer/services/llm.py`),
> not an agent runtime, and emits **no OTel traces** (OTel libs are present but dormant). So
> induction eval rests on COA's own scorers + its persisted artifacts + `COA/Ontology`
> CloudWatch metrics — all AWS-native, no new dependency.

## The three eval surfaces we found

### Surface 1 — Deterministic artifact scorers (COA built-in, verified in code)
`packages/ontology-engine/src/coa_ontology/`

| Scorer | Location | What it measures | Deterministic? |
|---|---|---|---|
| **HermiT** OWL reasoner | `validation/validators/tier1.py` | logical consistency, datatype validity | ✅ reasoner |
| **OntoQA** | `validation/validators/tier2.py` | metric-based ontology quality | ✅ metric |
| **OoPS!** pitfall scanner | `validation/validators/tier3.py` | ontology defects/pitfalls | ✅ rule-based |
| **SHACL** | `tests/.../test_shacl_constraints.py` | shape conformance | ✅ |
| **Golden-compare** | `benchmark/golden_compare.py` | **precision / recall / F1** of induced vs a reference "golden" ontology, per-class + per-property; match types exact/aligned/missing; tracks `grounded_to`/`grounded_tier` | ✅ set-overlap |

All five are reasoner/metric/set-overlap based — **not LLM-graded** — so they are themselves
repeatable, which matters for NFR-03.

### Surface 2 — Induce input/output capture (verified in schemas + store)
Everything keyed to `job_id`, persisted in DynamoDB + S3.

- **Input** (`InductionRequest`, `inducer/schemas.py`): `table_ids`, `strategy`
  (table_to_ontology / rigor_ontology / unstructured_lexical_graph), `grounding_mode`
  (NONE / STANDARD / ENHANCED), `grounding_ontology_ids`, `confidence_threshold`,
  `ontology_uri_prefix`, model config. → a run is fully reproducible.
- **Output artifacts** (`dynamo_store.put_proposal` → S3): `PROPOSAL#ontology` (the inferred
  **OWL/Turtle**), `PROPOSAL#r2rml` (table mappings), and a `user_reviewed_*` variant after
  human edit.
- **Input↔output link** (`InductionReport`): `matches: list[ConceptMatch]`, each carrying
  `matched_class_uri`, **`similarity` (numeric confidence)**, `match_type`, `relationship`
  (SKOS), and **`reason` (LLM rationale)**; plus `novel_classes_created`,
  `grounding_ontologies_used`, `dropped_tables`, `induced_ontology_id`.

### Surface 3 — Live CloudWatch metrics (verified live, agentops-publisher 195698601974)
Namespace **`COA/Ontology`**, emitted per induction job:
`InductionJobDurationMs`, `InductionJobCostUsd`, `BedrockInputTokens`/`OutputTokens`,
`RerankInvocations`, `RerankLatencyMs`, `TablesProcessed`, `ColumnsProcessed`,
**`GroundedClassesMatched`**, **`NovelClassesCreated`**, per-stage durations.
Induction logs: `/ecs/coa-dev-ontology-engine` (30-day retention). OOM rule:
`/aws/events/coa-dev-ontology-oom`.

---

## The mapping: IDBS requirement → eval surface

### Ontology Discovery
| Req | How we evaluate it | Surface | Status |
|---|---|---|---|
| FR-OD-01 infer ontology | run induction; output = `PROPOSAL#ontology` | S2 | ✅ |
| FR-OD-04 independent (no target) | `grounding_mode=NONE`; captured in input | S2 | ✅ |
| FR-OD-05 target-informed | `grounding_mode=ENHANCED` + `grounding_ontology_ids=[target]`; captured in input | S2 | ✅ |
| FR-OD-06 compare independent vs target-informed | diff the two runs' `PROPOSAL#ontology` + `InductionReport.matches` | S2 + S1(golden) | ⚠️ artifacts captured; diff is a script we write |
| FR-OD-07 rationale/confidence on inferred elements | `ConceptMatch.similarity` + `ConceptMatch.reason` per element | S2 | ✅ **CONFIRMED** (was [verify]) |
| FR-OD-08 traceability to source | `ConceptMatch.source_table/source_column/source_table_identity`; R2RML class→table | S2 | ✅ |
| FR-OD-09 evaluate inferred vs expected | **golden-compare precision/recall/F1** vs IDBS's expected ontology | S1 | ✅ **CONFIRMED** (was "manual") |
| FR-OD-10 compare vs industry standard | golden-compare / grounding against FIBO/Schema.org/etc. or an uploaded standard (AFO) | S1 + S2 | ✅ |

### Mapping & Alignment
| Req | How we evaluate it | Surface | Status |
|---|---|---|---|
| FR-OM-01 correspondence/difference inferred vs target | `InductionReport.matches` (grounded links) | S2 | ✅ |
| FR-OM-02 candidate mappings | `ConceptMatch` + `MatchCandidate` (top-K, scored) | S2 | ✅ |
| FR-OM-03 confidence per mapping | `ConceptMatch.similarity`, `MatchCandidate.fused_score` | S2 | ✅ **CONFIRMED** numeric field exists |
| FR-OM-04 rationale/evidence per mapping | `ConceptMatch.reason` (LLM justification) | S2 | ✅ **CONFIRMED** rationale persisted |
| FR-OM-05 inferred with no target match | `novel_classes_created`; `match_type="novel"` | S2 + S3(`NovelClassesCreated`) | ✅ |
| FR-OM-06 target with no inferred match | set-difference over target ontology vs matched URIs | S1/S2 | ⚠️ scripted post-step |
| FR-OM-07 human-in-the-loop, not auto-approved | proposal review workflow; AI vs `user_reviewed_*` artifacts | S2 | ✅ |
| FR-OM-08 quality vs expert mappings | IDBS specialists rate golden-compare + matches output | S1 + human | ⚠️ human (per eval's own method) |

### Outputs
| Req | How | Surface | Status |
|---|---|---|---|
| FR-OUT-01 OWL format | `PROPOSAL#ontology` is OWL/Turtle natively | S2 | ✅ |
| FR-OUT-02 independent & target-informed retained separately | two job_ids, two S3 artifacts | S2 | ✅ |
| FR-OUT-03 mapping artifact w/ confidence+rationale | `InductionReport.matches` (similarity + reason) | S2 | ✅ |

### Non-Functional + the "degree of human correction" theme
| Req | How | Surface | Status |
|---|---|---|---|
| NFR-02 run traceability | input request + output artifacts + report, all by `job_id`; `COA/Ontology` metrics | S2 + S3 | ✅ (note: 30-day log retention — longer sink if durable audit needed) |
| NFR-03 repeatability | re-run same `InductionRequest`; compare `GroundedClassesMatched`/`NovelClassesCreated` + golden-compare F1 across runs | S3 + S1 | ✅ measurable (induction is LLM-driven → expect variance; STANDARD/NONE modes are the low-variance control) |
| "degree of human interpretation/correction" (EC method, DO-06) | diff AI `PROPOSAL#ontology` vs `user_reviewed_*` | S2 | ✅ |

### Evaluation Criteria & Deliverables (how each is produced)
| EC / DO | Produced by |
|---|---|
| EC-01 known dataset → assessable vs expected | golden-compare F1 (S1) + HermiT consistency |
| EC-02 messy dataset → useful understanding | OntoQA/OoPS! quality (S1) + human read |
| EC-03 independent vs target-informed effect | two-run diff (S2) + golden-compare on each |
| EC-04/05 mappings w/ confidence+rationale for reviewer | `InductionReport.matches` (S2) |
| EC-06 traceability of inferences | `ConceptMatch` source fields + reason (S2) |
| EC-07 vs standard ontology | golden-compare vs standard (S1) |
| EC-08 reviewable path source→discovery→alignment | job_id chain: input → ontology → matches (S2) |
| DO-01/02 OWL artifacts | `PROPOSAL#ontology`, two runs (S2) |
| DO-03 independent vs target-informed comparison | scripted diff (S2) |
| DO-04/05 mapping artifact + confidence/rationale | `InductionReport.matches` (S2) |
| DO-06 criterion ratings + correction degree | human ratings on S1+S2 outputs |
| DO-07 repeatability assessment | re-run variance on S3 metrics + S1 F1 |
| DO-08 overall suitability | synthesis of all above |

---

## Net: coverage summary

- **Fully covered by existing, deterministic, AWS-native surfaces:** FR-OD-01/04/05/07/08/09/10,
  FR-OM-01/02/03/04/05/07, FR-OUT-01/02/03, NFR-02, EC-01/04/05/06/07/08, DO-01/02/04/05.
- **Covered but needs a thin script we write** (not a platform gap): FR-OD-06, FR-OM-06, DO-03
  (the two diff/set-difference reports).
- **Human by design** (matches the eval's own Evaluation Method): FR-OM-08, DO-06, DO-08, and the
  final Met/Partially/Not ratings.
- **The genuine risk, unchanged:** NFR-03 / DO-07 repeatability — induction is LLM-driven; quantify
  variance with S3 metrics, use STANDARD/NONE as the low-variance control, don't promise identical
  output.

The two earlier open build-items both reduce to small scripts, not platform work:
1. **independent-vs-target diff** (FR-OD-06/DO-03) and **unmatched-target set-difference** (FR-OM-06);
2. still-open `[verify]`: spreadsheet-JSON/Excel/instrument ingestion in `packages/sources/`
   (FR-DI-02) — the one real capability unknown left.
