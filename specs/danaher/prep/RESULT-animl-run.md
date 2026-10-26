# AnIML target-grounded run — RESULT (first real grounded run)

> The gate run. Executed 2026-10-26 on coa-dev (agentops-publisher / 195698601974), namespace
> `animl-afo` (3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9), job `b2c50933-a16f-41a8-91de-db9c14fdd93c`.
> Strategy `table_to_ontology`, `grounding_mode=ENHANCED`, grounding ontology
> `http://www.w3id.org/animl/ontology/` (AnIML/AFO). This is the run the Calanthir run never was —
> a loaded + embedded target in the namespace. Artifacts in S3 proposals/.../latest/.

## Headline: grounding works. 3/3 tables grounded, all exactMatch, with real rationale.

| Source table | → Target class | similarity | relationship | match_type |
|---|---|---|---|---|
| samples | `aml:Sample` | 0.95 | exactMatch | exact |
| measurements | `aml:Result` | 0.92 | exactMatch | exact |
| experiment_steps | `aml:ExperimentStep` | 0.95 | exactMatch | exact |

0 novel. Every mapping is semantically correct. The reranker considered the full candidate set
(e.g. for `samples`: Sample / SampleInContainer / Container / SampleSet / Tag / Infrastructure, lexical
~0.44–0.46) and correctly picked Sample at fused 0.95. Full pipeline (exact → embedding recall → LLM
rerank) proven on real data. `scoring_strategy: grounding_enhanced`.

Sample `reason` (real semantic justification, not a diagnostic string):
> "The samples table directly represents laboratory samples used in experiments with detailed
> attributes (pH, weight, composition, status), matching the Sample ontology class definition of 'an
> item which is used, or that data regarding which is used, in an experiment'."

## Findings for the COA eng sync — corrected by live evidence

### 2 — THE REAL GAP (VERIFIED): COA grounds classes to the target but NOT properties.
r2rml.ttl has 142 column mappings — every column IS mapped — but **all to minted `induced#` predicates,
ZERO to the target**: `grep rr:predicate` → 47 induced, 0 `aml:`.
```
rr:column "device_manufacturer" → rr:predicate ind:experimentSteps_deviceManufacturer  (induced, not aml:)
```
**Control test that makes this a verified gap, not a thin-target artifact:** 5 source columns have an
EXACT/near-exact AnIML property to bind to — `name`→`aml:name`, `comment`→`aml:comment`,
`timestamp`→`aml:timeStamp`, `value`→`aml:value`, `unit`→`aml:hasUnits`. COA bound **none** of them to
the target; all 5 still became `ind:` predicates. So the target HAD the properties and COA did not use
them — the grounding pipeline operates at the **class level only**; column→property grounding against the
target is not happening.

(Note on AnIML as a target: it is a STRUCTURAL/document ontology — Sample, Parameter, Category, Unit — so
most domain columns like `Initial pH`, `Mixer ID`, `Lot Number` genuinely have no AnIML property and
minting induced predicates for THOSE is correct. The gap is proven only by the 5 columns that DID have an
exact target match and still were not grounded.)

The Danaher use case's core requirement is column/field-level mapping to the target
(`Titer_Value → Result_Value`). COA grounds the table layer to the target but mints induced predicates for
the column layer even when a target property exists. **The precise, evidence-backed ask:** can the
grounding pipeline ground PROPERTIES against the target, not just classes? Evidence: `value`/`name`/`unit`

### 2b — HOW the change would work + per-strategy state (verified in code)
The recall half ALREADY EXISTS; what's missing is turning it into a scored binding.
- **Property embeddings are indexed** on ingest (`entity_type="property"`), so target properties
  (`aml:value`, `aml:name`, `aml:hasUnits`) are searchable in the pool.
- **`table_to_ontology`** (the path run here): tables call `grounding_svc.ground_table` (recall→rerank→
  bind a scored `ConceptMatch`). Columns do NOT call grounding at all — they only mint `ind:` predicates
  (`table_to_ontology.py:~220`: "column-level matching reads only its OWN table identity's grounding
  result"). THIS is why `value` never reached `aml:value`.
- **RIGOR** (`rigor_ontology.py:_retrieve_ontology_context`, ~600): DOES
  `search_embeddings(entity_type="property")` per column — but only to build a human-readable RAG block
  ("column 'value' → aml:value (similarity=0.9)") fed to the generator LLM. Reuse is the LLM's discretion,
  NOT a deterministic scored binding, and not recorded as a column→property `ConceptMatch`.

So NEITHER strategy emits a first-class scored column→target-property match (with similarity +
relationship) the way `ground_table` does for classes. **Refined ask:** property recall exists (indexed
embeddings + RIGOR's per-column search) — can column grounding be promoted to a first-class SCORED binding
(recall→rerank→bind→record, fallback to minted `ind:` only when recall is empty), reusing the recall
that's already there? This is closing the last mile, not building from scratch.

had exact AnIML matches and still minted induced predicates.

### 1a — `relationship` + `reason` PERSIST on this path. Ask softens.
Both populated 3/3 on `table_to_ontology` (grounding_enhanced). The drop we found in code is on the
**RIGOR** construction site (`rigor_ontology.py:841/855`), NOT the grounding path used here. So the ask is
no longer "please wire relationship/reason" — it is "confirm it's wired on all paths; why does RIGOR
drop it". The equivalence flag (skos:exactMatch) IS in the output today for the grounding path.

### matched_count discrepancy — not reproduced.
This run is cleanly 3 grounded / 0 novel, internally consistent. The earlier Calanthir 7-vs-13 oddity
looks specific to that broken (empty-pool) run.

### Transformations — unchanged.
No CONVERT_UNIT / value transforms (expected — the component boundary; lives in the eval harness /
Mapper layer, not COA).

## Scorecard-style summary
| Capability | Status |
|---|---|
| Grounding path runs end-to-end | ✅ proven |
| Table → target class, with confidence | ✅ 3/3 exact, 0.92–0.95 |
| Rationale (`reason`) | ✅ real semantic justification |
| Equivalence flag (`relationship`) | ✅ 3/3 exactMatch |
| Column → target property | ❌ minted induced predicates instead |
| Transformation expression | ❌ not COA (harness/Mapper) |

## Artifacts (S3, agentops-publisher)
`s3://coa-dev-ontology-artifacts-195698601974/proposals/3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9/b2c50933-a16f-41a8-91de-db9c14fdd93c/latest/`
— matches.json, ontology.ttl, r2rml.ttl (142 column maps), constraints.ttl.
