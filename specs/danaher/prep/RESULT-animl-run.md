# AnIML target-grounded run — RESULT

> **Two runs are recorded here.** Read §0 first: it is the VERIFIED END-TO-END PROOF after the
> column→property-grounding fix shipped to coa-dev. The original run (§ "first real grounded run",
> below) is kept verbatim as the DIAGNOSIS that led to the fix — its scorecard line
> "Column → target property ❌" is now SUPERSEDED by §0.

---

# §0 — VERIFIED END-TO-END PROOF (post-fix) — 2026-10-26 ~21:2x ET

> Second run, AFTER the fix (`feat/column-property-grounding`, commit `dea2a8b`) was deployed to
> coa-dev. Same env (agentops-publisher / 195698601974), same namespace `animl-afo`
> (3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9), same strategy `table_to_ontology` + `grounding_mode=ENHANCED`,
> same grounding ontology `http://www.w3id.org/animl/ontology/`. Proposal job
> `25416fff-d3d5-4f65-b101-6cfbf3d6f9cb`. This is the full arc working: **AnIML source → structural
> induction → target-grounded COLUMNS → accepted/published ontology → VKG reload → governed NL query
> answering in the customer's domain terms.**

## The fix (what shipped)
The gap diagnosed below (columns matched raw-cosine-only, no exact-name shortcut, no LLM rerank, so
identical-name matches scored ~0.45 → novel → never reached the `owl:equivalentProperty` gate) was
closed by routing column matching through a new `ground_property()` (recall + exact-name +
ENHANCED rerank), mirroring `ground_table`. Code in `grounding.py` (`ground_property`,
`_RERANK_SYSTEM_PROPERTY`, `entity_type` threaded through `ground_table`/`_recall`) and `pipeline.py`
(column second-pass routed through `grounding_svc.ground_property`). Full unit suite 3009 passed.

## Proof 1 — columns now ground to target properties (was 0, now 34)
`ontology.ttl` from job `25416fff` contains **34 `owl:equivalentProperty` axioms** (pre-fix run: **0**).
Every source column maps to an AnIML AFO property. Representative mappings:

| Source column | → AnIML AFO target property | Note |
|---|---|---|
| `measurements.value` | `aml:pointValue` | the flagship pre-fix miss — now mapped |
| `measurements.unit` | `aml:hasUnits` | |
| `measurements.parameterName` / `resultName` / `categoryName` | `aml:name` | |
| `measurements.parameterType` | `aml:DataType` | |
| `samples.name` | `aml:name` | |
| `samples.created` / `manufactured`, `experimentSteps.timestamp` | `aml:timeStamp` | |
| `samples.lotNumber` / `ingredientId` / `mixerId` / `sampleId` | `aml:identifier` | |
| `samples.amountAdded` / `startWeight` / `endWeight` / `finalPh` | `aml:quantity` | |
| `samples.owner` | `aml:organisation` | semantic, non-exact |
| `samples.group` / `type` | `aml:hasSampleCategory` | semantic, non-exact |
| `samples.comment` | `aml:comment` | |
| `samples.link` | `aml:pointsTo` | |
| `experimentSteps.samplePurpose` | `aml:hasUseCase` | semantic, non-exact |
| `experimentSteps.sampleRole` | `aml:requiresRole` | semantic, non-exact |

The ENHANCED reranker resolved BOTH exact-name matches (`value`→`pointValue` via the restored
exact-name signal) AND semantically non-trivial ones (`samplePurpose`→`hasUseCase`,
`sampleRole`→`requiresRole`, `group`/`type`→`hasSampleCategory`, `owner`→`organisation`). Targets are
written as FULL URIs (`http://www.w3id.org/animl/ontology/<prop>`), not an `aml:` prefix alias —
valid Turtle, semantically correct; the ontology.ttl just doesn't declare the `aml:` alias (cosmetic).

Tables still ground 3/3 (grounding_enhanced): samples→`aml:Sample` (0.95 exactMatch),
measurements→`aml:Result` (0.85 closeMatch), experiment_steps→`aml:ExperimentStep` (0.95 exactMatch).

## Proof 2 — accept published the governed ontology
Accepting the proposal in the UI wrote
`s3://coa-dev-ontology-artifacts-195698601974/ontologies/3a6c6a53-.../latest/` at 21:22:49:
`ontology.ttl` (the `aml:` equivalences), `mappings.r2rml`, `schema.sql`, plus `induced/induced.ttl`
(104 KB). R2RML is 3 TriplesMaps (samples/measurements/experiment_steps) wired to the live Glue
source (`datasourceId 0c235c56-...`, `sourceSchema animl_afo_seed`), 47 `rr:predicate` column maps.
**Design note:** R2RML predicates point to the INDUCED predicates (`ind:measurements_value`), NOT
`aml:`. The `aml:` alignment lives in `ontology.ttl` as `owl:equivalentProperty`. Two-layer model:
R2RML materializes raw data under stable `ind:*` predicates; `ontology.ttl` carries the semantic
bridge to AFO terms.

## Proof 3 — VKG reloaded (Ontop virtual graph)
Accept triggered the per-namespace Ontop ECS service `coa-dev-vkg-3a6c6a53-...`: went IN_PROGRESS at
21:24:29, reached `COMPLETED` 1/1 steady state. This is a VIRTUAL knowledge graph — SPARQL→SQL over
the live Glue source at query time, no bulk triple load (so "triple count" is not the metric;
query resolution is). `schema.sql` correctly serves all three tables with every column.

## Proof 4 — governed NL query answered in DOMAIN TERMS (the money shot)
UI Playground, principal `human subhaviv@amazon.com`, namespace `3a6c6a53-...`, model
`us.anthropic.claude-sonnet-5`, request `e3b23fba-5ef1-4e46-9368-4247a7154ba0`:

> Q: *"For each measurement, give me the result name, the point value, and the units."*
> A: **304 row(s) returned**, 4221 ms total.

Tier trace:
- **Tier outcome: Tier 2 — ontology.** `Sql Generate` confidence 0.8, `tables: ["measurements"]`.
- Boundary controls: **Cedar: allow · SQL Firewall: ok**. `Sql Execute` rowCount 304, `shot: 1`
  (first-try correct SQL), `truncated: false`, `engine: athena`.
- Routing skipped tier3 vector search; `hasStructuredSource: true`.

**How it resolved (be precise):** this took the **Tier 2 text-to-SQL** path — the ontology/grounding
context (induced ontology + the column→property mappings/labels) fed the SQL generator, which is how
the agent knew "point value" → the `value` column and "units" → `unit`. It did **NOT** go through
Ontop SPARQL + `owl:equivalentProperty` inference (no compiled SPARQL artifacts; `engine: athena`).
So the grounding *informs* NL→governed-SQL; whether the VKG does equivalentProperty inference at
SPARQL query time is a SEPARATE, still-open question (this answer didn't need it).

## §0 scorecard (post-fix, verified)
| Capability | Status |
|---|---|
| Grounding path runs end-to-end | ✅ proven (two runs) |
| Table → target class, with confidence + rationale + relationship | ✅ 3/3 |
| **Column → target property (`owl:equivalentProperty`)** | ✅ **34 axioms (was 0) — FIXED** |
| Semantic (non-exact) column matches | ✅ e.g. samplePurpose→hasUseCase |
| Accept → published governed ontology | ✅ ontologies/.../latest/ written |
| VKG reload → serving | ✅ Ontop service COMPLETED 1/1 |
| Governed NL query in domain terms | ✅ 304 rows, Tier 2 ontology, Cedar+Firewall |
| SPARQL/VKG equivalentProperty inference at query time | ❓ not tested (NL path didn't need it) |
| Transformation expression (unit convert) | ❌ not COA (harness/Mapper layer) |

## §0 artifacts (S3, agentops-publisher)
- Proposal: `.../proposals/3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9/25416fff-d3d5-4f65-b101-6cfbf3d6f9cb/latest/`
  (matches.json, ontology.ttl [34 equivalentProperty], r2rml.ttl, constraints.ttl)
- Published: `.../ontologies/3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9/latest/`
  (ontology.ttl, mappings.r2rml, schema.sql)

---

# AnIML target-grounded run — RESULT (first real grounded run)

> The gate run. Executed 2026-10-26 on coa-dev (agentops-publisher / 195698601974), namespace
> `animl-afo` (3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9), job `b2c50933-a16f-41a8-91de-db9c14fdd93c`.
> Strategy `table_to_ontology`, `grounding_mode=ENHANCED`, grounding ontology
> `http://www.w3id.org/animl/ontology/` (AnIML/AFO). This is the run the Calanthir run never was —
> a loaded + embedded target in the namespace. Artifacts in S3 proposals/.../latest/.
>
> **NOTE: the "Column → target property ❌" line in this run's scorecard is SUPERSEDED by §0 above —
> the gap diagnosed here was fixed and verified.**

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
