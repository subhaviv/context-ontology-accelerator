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

### 2 — THE REAL GAP (FINAL, VERIFIED): column→property alignment is implemented but no column clears the confidence gate.
**This supersedes two earlier reads of this run.** Walking the code end-to-end (not just the output):

1. The ontology builder ALREADY emits column→target-property alignment —
   `table_to_ontology.py:787-792`:
   ```python
   if cm and cm.match_type in ("exact", "high_confidence") and cm.matched_class_uri:
       g.add((prop_uri, OWL.equivalentProperty, URIRef(cm.matched_class_uri)))
       if cm.similarity is not None:
           g.add((prop_uri, match_confidence, Literal(cm.similarity, XSD.float)))
   ```
   So the feature EXISTS, confidence-gated, emitting `owl:equivalentProperty`. There is **no missing
   emission code and no builder change to make.**

2. It never fired: `ontology.ttl` has `owl:equivalentProperty` count = 0, and `matchConfidence` only on
   the 3 CLASSES (0.92/0.95/0.95), none on any column. So every column's `ConceptMatch.match_type` came
   back BELOW `high_confidence`.

3. Why: column recall is pure cosine with **no exact-name shortcut** (`pipeline.py` `_get_candidates` →
   `_search_one` → `_cosine_similarity`; the TABLE path has an exact-name→1.0 shortcut, the COLUMN path
   does not). `_classify` bands: `≥0.95` exact, `≥0.80` high_confidence, `≥0.50` ambiguous, else novel.
   The AnIML columns recalled ~0.44-0.46 — even `value`↔`aml:value` (identical names) — so they
   classified `novel` and the gate correctly skipped them.

**So the gap is RECALL/SCORING, not emission or missing feature.** Short column tokens (`value`, `name`,
`unit`) embed weakly against a property whose embedding text is a long definition, and there is no
exact/normalized-name boost for columns, so identical-name matches score ~0.45 and never reach the
alignment gate that already exists.

### 2b — The actual fix (small, in recall — NOT the builder)
Add a column exact/normalized-name shortcut mirroring the table path: when a source column name normalizes
equal to a target property's label/local-name, boost that candidate's score (to ~1.0 / `exact`) so it
clears the gate and the EXISTING builder code (`:787`) emits `owl:equivalentProperty`. Change is in the
column recall/scoring (`pipeline.py` `_pick_winner`/`_get_candidates` or a normalized-name pass), not the
emission. Everything downstream of emission is already wired. RIGOR (`_retrieve_ontology_context`) also
recalls target properties per column but only as LLM RAG context, so it has the same no-shortcut weakness.

**Corrected ask to the eng team:** column→property alignment is implemented and confidence-gated
(`owl:equivalentProperty`, `:787`), but no column cleared the gate — even exact-name matches like
`value`↔`aml:value` recall at ~0.45 because the column path has no exact-name shortcut (the table path
does). Can column grounding get an exact/normalized-name boost so identical-name matches clear the gate?

### 2c — ROOT CAUSE: table grounding is a 3-layer pipeline; column matching is 1 layer.
This asymmetry is why `samples→aml:Sample` scored 0.95 but `value→aml:value` died at 0.45.

| Step | Table (`ground_table`, grounding.py) | Column (`_pick_winner`, pipeline.py) |
|---|---|---|
| 1. Embedding recall (cosine) | yes | yes |
| 2. Exact-name check (`_exact_name_matches`) | **yes** — unique exact name auto-grounds (STANDARD) or feeds the signal to the reranker (ENHANCED) | **no** |
| 3. LLM rerank (Claude Haiku judges the match, returns confidence + relationship) | **yes** | **no** |

- A table's final score is the **LLM reranker's** verdict: it recognized `samples` ≈ `Sample` and returned
  0.95/`exact`, overriding the weak ~0.45 raw cosine.
- A column's final score **is** the raw cosine (~0.45) — `_pick_winner` just takes the top candidate and
  `_classify`s it. Nothing lifts an identical-name match, so it lands `novel` and never reaches the
  alignment gate at `:787`.

So the gap is structural: **columns are missing the exact-name boost AND the LLM rerank that tables have.**
This also explains why input-shaping (enriching Glue column descriptions) is an unreliable workaround — it
nudges raw cosine but there is no reranker to *recognize* the match the way tables get. The robust fix is
to give columns the same exact-name/rerank treatment tables already have.

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
