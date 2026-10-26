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

### 2 — THE REAL GAP (VERIFIED, CORRECTED): column-level SKOS alignment axioms are not emitted.
**Important correction to an earlier read of this run.** COA aligns induced terms to the target via SKOS
axioms in the induced ontology — it does NOT (and should not) rename R2RML predicates to the target. The
induced namespace (`ind:`) is the PROPOSAL; grounding annotates it with `skos:exactMatch` links. Minting
`ind:` predicates in R2RML is therefore CORRECT — Ontop serves this customer's data on the induced
vocabulary, and the target binding lives in the alignment axioms.

Verified in `ontology.ttl` (job b2c50933): exactly 3 alignment axioms, ALL class-level —
```
<ind:Measurements>    skos:exactMatch  aml:Result
<ind:ExperimentSteps> skos:exactMatch  aml:ExperimentStep
<ind:Samples>         skos:exactMatch  aml:Sample
```
**ZERO column/property-level alignment axioms.** The column second-pass in `pipeline.py` (~line 640)
DOES compute a `ConceptMatch` with `matched_class_uri` = the target property (e.g. `aml:value` for the
`value` column), but the ontology builder never writes the corresponding
`ind:measurements_value skos:exactMatch aml:value`. So field-level target mappings are COMPUTED and then
DROPPED — classes get alignment axioms, columns do not.

**Why this is the right framing (not "make R2RML speak aml:"):** the migration field→field deliverable
(`Titer_Value → Result_Value`) is derivable from the alignment axioms — the governed, SKOS way. The fix
is to MIRROR the class-alignment emission for columns; it does NOT touch R2RML, SQL, or Ontop, so there is
no SHACL/range/join blast radius. The induced predicates stay; we only ADD column alignment axioms.

(Note on AnIML as a target: it is a STRUCTURAL/document ontology, so most domain columns — `Initial pH`,
`Mixer ID`, `Lot Number` — genuinely have no AnIML property and SHOULD stay induced-only with no axiom.
The gap is proven by the columns that DID have an exact target match — `value`→`aml:value`,
`name`→`aml:name`, `timestamp`→`aml:timeStamp`, `unit`→`aml:hasUnits` — and still got no alignment axiom.)

### 2b — The fix, and per-strategy/recall state (verified in code)
- **Recall exists:** property embeddings are indexed on ingest (`entity_type="property"`), and the column
  second-pass (`pipeline.py` ~640, `_get_candidates(..., "property", ontology_id=scoped)`) recalls target
  properties and `_pick_winner` returns a `ConceptMatch` carrying the target `matched_class_uri`.
- **Emission is the gap:** the builder emits class alignment axioms but NOT column ones. So the match
  reaches the builder and is not turned into an axiom.
- **Scope of the fix:** in the ontology builder, when a column's `ConceptMatch` has `matched_class_uri`
  and `match_type in (exact, high_confidence)`, emit `<ind:col> skos:exactMatch <target property>` —
  exactly mirroring the class path. Gate on confidence (short column names score low, ~0.45, so a loose
  gate would mis-align `device_manufacturer`→`aml:name`). R2RML unchanged.
- **RIGOR** (`rigor_ontology.py:_retrieve_ontology_context`): recalls target properties per column only as
  RAG context for the generator LLM — not a recorded alignment either.

So the corrected ask: **class grounding emits `skos:exactMatch` alignment axioms; column grounding
computes the match but emits no axiom. Mirror the class-alignment emission for columns (confidence-gated),
leaving R2RML induced predicates intact.**

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
