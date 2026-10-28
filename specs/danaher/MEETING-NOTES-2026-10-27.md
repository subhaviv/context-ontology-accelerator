# Meeting Notes — COA Column→Target-Property Grounding (Danaher/IDBS eval)

> **Meeting date:** 2026-10-27
> **Prepared:** 2026-10-26 (late), from the work done this session.
> **Audience:** AWS COA engineering team.
> **One line:** COA now does source→target *column→property* migration mapping end-to-end — the last
> gap blocking the Danaher/IDBS story is fixed, deployed to coa-dev, and proven with a live governed
> query. This note is the complete record: what, why, how, proof, open questions, next steps.

---

## 1. TL;DR (read this if nothing else)

- **What I changed:** added column→target-property grounding to the ontology engine. COA now emits
  `owl:equivalentProperty` axioms linking source columns to target-ontology properties
  (e.g. `measurements.value` → `aml:pointValue`, `measurements.unit` → `aml:hasUnits`).
- **Why it was needed:** COA could map source *tables* → target *classes*, but *columns* → *properties*
  always came back "novel" — even identical names like `value`↔`aml:value`. Result: **zero**
  column-level equivalences in the induced ontology. That is the *core* of a source→target migration
  mapping (the Danaher/IDBS use case). Without it COA could say "this table is a Sample" but not
  "this column is the pointValue" — i.e. no migration mapping.
- **Root cause:** pipeline asymmetry — table grounding is 3 layers (recall → exact-name → LLM rerank),
  column matching was 1 layer (raw cosine only). Short column names scored ~0.45 → "novel" → never
  reached the `owl:equivalentProperty` gate (which already existed).
- **The fix:** give columns the same 3-layer path as tables. Branch `feat/column-property-grounding`,
  commit `dea2a8b`. 3009 unit tests pass.
- **Proof:** live coa-dev run → **34 column equivalence axioms** (was 0) → accept → VKG reload →
  governed NL query returned **304 rows** (Tier 2 ontology, Cedar + SQL Firewall ok).

---

## 2. The problem (what was broken and why it mattered)

The Danaher/IDBS use case is a **source→target migration mapping**: take messy source exports, map them
onto a supplied target ontology (AnIML/AFO), and produce migration-ready recommendations with confidence
and rationale. The mapping is only useful at the **column→property** level — "this source column
corresponds to this target property." Table-level grounding ("this table is a Sample") is necessary but
not sufficient.

COA's induced ontology was coming back with **zero `owl:equivalentProperty` axioms** — no column
mappings at all — even though table grounding worked perfectly (3/3 tables, 0.92–0.95 confidence). That
made COA look like it couldn't do the one thing the use case is about.

---

## 2b. Why the source is exactly 3 tables — the domain rationale (anticipate this question)

**The "3" is not a sampled slice of a bigger thing — it is the natural shape of analytical lab data.**
AnIML (ASTM E2911) and its successor AFO (Allotrope Foundation Ontology) model one question —
*"what was measured, on what, during what procedure"* — which decomposes into exactly three
irreducible primitives. Every analytical result is a combination of them:

| Table | AFO class | Role | Domain meaning | In our data |
|---|---|---|---|---|
| `samples` | `aml:Sample` | the NOUN | the physical material studied (formulation, compound, batch) + its intrinsic properties (pH, weight, lot, owner). Exists *before* any experiment. | 42 rows |
| `experiment_steps` | `aml:ExperimentStep` | the VERB | a single action in the experiment — which method, which device, when, the sample's role. The PROCESS node linking a sample to the act of measuring. | 63 rows |
| `measurements` | `aml:Result` | the OUTPUT | the quantitative data a step produced — parameter + value + unit (`3.395 g`, `47.9 µm`, `2400 rpm`). Where the actual numbers live. | 304 rows |

**Why this triad is the CORRECT decomposition (not a convenience cut):**
- **Subject → process → object is the spine of all scientific data.** Strip any one and a measurement
  can't be described: a value with no sample is meaningless; a sample with no result is just inventory;
  a step with neither is an empty procedure. AnIML's own schema is built on `SampleSet` /
  `ExperimentStepSet` (with `Result` nested under steps) for exactly this reason.
- **It's a star schema, which is why it grounds cleanly.** `experiment_steps` is the fact table;
  `samples` joins by `sample_id`; `measurements` joins by `experiment_step_id`. That FK-linked shape is
  exactly what `table_to_ontology` expects — which is why 3/3 grounded at 0.85–0.95.
- **It isolates the unit-conversion seam — the Danaher money problem.** Value and unit live together in
  `measurements` (`value`=`2400`, `unit`=`rpm`), deliberately surfacing the `mg/L → mg/mL` transformation
  boundary the eval must grade, instead of burying units inside sample rows.

**So we did NOT map "3 of 46 classes."** We mapped the full source (all 42 samples / 63 steps / 304
measurements, every column) onto the *subset of the target the source actually touches*. The other 43
AFO classes (Series, Unit systems, Technique taxonomies, equipment) are refinements around this spine
that a single formulation export does not instantiate — mapping to them would mean inventing source data
that doesn't exist. This is also why `golden_compare.py` recall against the full 46-class golden
(3/46 = 0.043) is a **misleading denominator** (see §7b): the right denominator is the touched subset.

**Known limitation (state it honestly):** this is one real-but-small public AnIML dataset (KE-UniLiv
Unilever examples), a rehearsal to prove the pipeline before IDBS brings real, larger data. Breadth
(more instrument exports, wider schemas) and repeatability across datasets are NOT yet proven — a
Phase-0 onsite item.

---

## 3. Root cause (the real finding, corrected through code + live evidence)

The emission code was NOT missing. `table_to_ontology.py:787` already emits
`owl:equivalentProperty` when a column match is `exact`/`high_confidence`. The problem was upstream: **no
column ever cleared the confidence bar to trigger it.**

**Structural asymmetry between table grounding and column matching:**

| Step | Table (`ground_table`, grounding.py) | Column (pre-fix: `_pick_winner`, pipeline.py) |
|---|---|---|
| 1. Embedding recall (cosine) | ✅ | ✅ |
| 2. Exact-name check (`_exact_name_matches`) | ✅ unique exact name auto-grounds / feeds reranker | ❌ none |
| 3. LLM rerank (Claude judges, returns confidence + relationship) | ✅ | ❌ none |

- A **table's** final score is the LLM reranker's verdict: it recognized `samples` ≈ `Sample` and
  returned 0.95, overriding the weak ~0.45 raw cosine.
- A **column's** final score (pre-fix) *was* the raw cosine (~0.45). Short tokens (`value`, `unit`,
  `name`) embed weakly against a property whose embedding is a long definition. `_classify` bands:
  ≥0.95 exact / ≥0.80 high_confidence / ≥0.50 ambiguous / else novel. So even `value`↔`aml:value`
  (identical names) landed "novel" and never reached the emission gate.

**Conclusion:** the gap was in recall/scoring, not emission. Input-shaping (enriching Glue column
descriptions) is an unreliable workaround because it only nudges cosine — there was no reranker to
*recognize* the match the way tables get one. The robust fix is to give columns the same path tables have.

---

## 4. The fix (what I did)

**Branch:** `feat/column-property-grounding` · **Commit:** `dea2a8b` (code) + docs commits.

- `grounding.py`: new **`ground_property()`** wrapper (mirrors `ground_class`, for columns→properties)
  and **`_RERANK_SYSTEM_PROPERTY`** system prompt; threaded an **`entity_type`** parameter through
  `ground_table` and `_recall` so recall searches the correct embedding space (property vs class).
- `pipeline.py`: routed the column second-pass through `grounding_svc.ground_property` — so columns now
  get **recall + exact-name shortcut + LLM rerank**, exactly like tables.
- Tests: updated 3 test doubles + added a capability test proving column→target-property grounding.
- **Full unit suite: 3009 passed, 0 failed. ruff + mypy green.**

Net effect: an identical-name or semantically-close column match is now recognized by the reranker,
clears the gate, and the existing `:787` emission fires `owl:equivalentProperty`.

---

## 5. Proof — verified end-to-end on coa-dev (2026-10-26)

Env: `agentops-publisher` (195698601974), us-east-1. Namespace `animl-afo`
(3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9). Strategy `table_to_ontology`, `grounding_mode=ENHANCED`,
grounding ontology `http://www.w3id.org/animl/ontology/`. Proposal job
`25416fff-d3d5-4f65-b101-6cfbf3d6f9cb`.

**Proof 1 — columns now ground (0 → 34).** `ontology.ttl` has **34 `owl:equivalentProperty` axioms**.
Representative:

| Source column | → AnIML AFO property |
|---|---|
| `measurements.value` | `aml:pointValue` |
| `measurements.unit` | `aml:hasUnits` |
| `measurements.parameterName`/`resultName`/`categoryName` | `aml:name` |
| `samples.created`/`manufactured`, `experimentSteps.timestamp` | `aml:timeStamp` |
| `samples.lotNumber`/`ingredientId`/`sampleId` | `aml:identifier` |
| `samples.amountAdded`/`startWeight`/`finalPh` | `aml:quantity` |
| `samples.owner` | `aml:organisation` (semantic) |
| `samples.group`/`type` | `aml:hasSampleCategory` (semantic) |
| `experimentSteps.samplePurpose` | `aml:hasUseCase` (semantic) |
| `experimentSteps.sampleRole` | `aml:requiresRole` (semantic) |

The reranker resolved BOTH exact-name and semantically non-trivial matches. Tables still ground 3/3
(samples→Sample 0.95, measurements→Result 0.85, experiment_steps→ExperimentStep 0.95).

**Proof 2 — accept published the governed ontology.** Wrote
`s3://coa-dev-ontology-artifacts-195698601974/ontologies/3a6c6a53-.../latest/` (ontology.ttl,
mappings.r2rml, schema.sql) + `induced/induced.ttl` (104 KB). R2RML = 3 TriplesMaps wired to live Glue
(`sourceSchema animl_afo_seed`), 47 column maps.

**Proof 3 — VKG reloaded.** Per-namespace Ontop ECS service `coa-dev-vkg-3a6c6a53-...` → `COMPLETED`
1/1. Virtual graph (SPARQL→SQL over Glue at query time; no bulk triple load).

**Proof 4 — governed NL query in domain terms (the money shot).** UI Playground, principal
`subhaviv@amazon.com`, model `us.anthropic.claude-sonnet-5`, request
`e3b23fba-5ef1-4e46-9368-4247a7154ba0`:

> Q: *"For each measurement, give me the result name, the point value, and the units."*
> A: **304 rows**, 4221 ms, **Tier 2 — ontology**, `Sql Generate` conf 0.8, **Cedar: allow**,
> **SQL Firewall: ok**, first-shot SQL (`shot: 1`), `engine: athena`.

**How it resolved (precise):** the Tier-2 **text-to-SQL** path — the ontology/grounding context fed the
SQL generator, so the agent knew "point value" → `value` column, "units" → `unit`. It did **not** go
through Ontop SPARQL + `owl:equivalentProperty` inference (no compiled SPARQL; `engine: athena`). So
grounding *informs* NL→governed-SQL.

Full detail: `specs/danaher/prep/RESULT-animl-run.md` §0.

---

## 6. Design note (for the team to confirm)

Two-layer predicate model, by design:
1. **R2RML** materializes raw data under stable **induced** predicates (`ind:measurements_value`).
2. **`ontology.ttl`** carries the semantic bridge to AFO terms as `owl:equivalentProperty`
   (`ind:measurements_value ≡ aml:pointValue`).

`aml:` targets are written as full URIs, not an `aml:` prefix alias (valid Turtle; the ontology.ttl
just doesn't declare the alias — cosmetic).

---

## 7. Open questions for the team

1. **Upstream the prototype?** Branch is on my fork (`github.com/subhaviv/context-ontology-accelerator`).
   PR, or re-implement in your style? The load-bearing decision is the *approach* (mirror
   `ground_table`'s 3-layer path for properties); the code is negotiable.
2. **Two-layer predicate design confirmed?** (§6) Intended that R2RML uses `ind:*` and the `aml:`
   alignment lives only in the equivalence axioms?
3. **Does the VKG do `owl:equivalentProperty` inference at SPARQL query time?** The NL (Tier 2) path did
   NOT need it — grounding fed SQL-gen directly. But for a "query in target terms over SPARQL" story we
   need to know if `aml:pointValue` resolves via the equivalence bridge, or only `ind:*` predicates do.
   **Untested so far.**
4. **ASK 1a (`relationship`/`reason`)** — confirmed ALREADY wired on the grounding path (3/3 populated).
   Only RIGOR (`rigor_ontology.py:841/855`) drops them, and RIGOR doesn't ground. Low priority.

(ASKs 1b transformation-field, 1c model-version-stamp, and ASK 2 scope decisions from
`COA-ENG-SYNC-BRIEF.md` still stand unchanged.)

---

## 7b. Eval reuse — the repo already ships the quality spine (don't build a parallel harness)

Inventoried `packages/` (cdk.out build copies excluded). COA already has sanctioned eval/validation
machinery that maps onto the Danaher success criteria — **this resolves ASK 2.3** ("is there an official
eval methodology?").

| Repo asset | What it does | Danaher reuse | Reuse level |
|---|---|---|---|
| `packages/ontology-engine/src/coa_ontology/benchmark/golden_compare.py` | Compares an induced ontology vs a **golden/reference** ontology → per-class AND per-property **precision / recall / F1**, match tiers `exact` / `aligned` (fuzzy local-name) / `missing`, tracks `grounded_to` + `grounded_tier`. Driven from `scripts/live_induction_run.py`. | Feed IDBS target as "golden", run induction, score coverage + precision. **This is the Day-2 scorecard, free.** | ⭐ HIGH |
| `src/coa_ontology/validation/routers/validate.py`, `tests/unit/test_shacl_constraints.py`, `test_validation.py` | SHACL ontology-shape validation on the structured path | "Is the induced ontology structurally valid" dimension | MEDIUM |
| `tests/unit/test_tier1_hermit_datatype_sanitize.py` | HermiT reasoning / consistency | Consistency check of the induced ontology | MEDIUM |
| `packages/context-manager/src/coa_serve/tier2/ontop/sparql_validator.py` (+5 tests) | Validates Tier-2 SPARQL-over-Ontop | Directly relevant to the open §7.3 question (does the VKG do equivalentProperty inference) | MEDIUM |

**Critical caveat (golden_compare.py says so itself):** it scores **structural overlap only** — "did the
pipeline find a concept the golden declares" — **NOT semantic correctness.** It will confirm `value` and
`pointValue` both exist and aligned; it will NOT confirm the mapping is *right*. Semantic correctness
still requires IDBS SMEs (already baked into the onsite run-of-show).

**What is NOT in the repo (we still build it) — a thin WRAPPER, not a new harness:**
- Transformation / Mapper stand-in (unit convert, date parse) — explicitly NOT COA; lives in the harness.
- Negative-test rows ("flag-don't-guess") and repeatability (3-run confidence stability) — wrap around
  `golden_compare.py`, not a replacement for it.

**Revised position for the team:** reuse `golden_compare.py` + SHACL + HermiT as the quality spine; build
ONLY the negative-test / repeatability / transformation wrapper around them. No parallel harness.

---

## 8. Next steps / action items

| # | Action | Owner |
|---|---|---|
| 1 | Push `feat/column-property-grounding` to fork so the team can see the diff | Subhaviv |
| 2 | Team decides: PR the prototype vs. re-implement | COA eng |
| 3 | Answer the SPARQL/VKG equivalentProperty-inference question (§7.3) | COA eng + us |
| 4 | (Optional) investigate why `848487018305` also has a parallel `coa-dev` env | Subhaviv |
| 5 | Carry this into the Nov onsite as a proven Day-1 demo | Subhaviv |

---

## 9. Reference pointers

- `specs/danaher/prep/RESULT-animl-run.md` §0 — full verified proof (all 4 proofs + scorecard + artifacts).
- `specs/danaher/COA-ENG-SYNC-BRIEF.md` — the located-in-code change requests + the status update banner.
- `specs/danaher/ONSITE-RUN-OF-SHOW-nov.md` — Phase-0 de-risk update (rehearsal DONE, demo script ready).
- Live artifacts (S3, agentops-publisher):
  - proposal `.../proposals/3a6c6a53-.../25416fff-.../latest/`
  - published `.../ontologies/3a6c6a53-.../latest/`
- COA dev: Web `https://d3upg0x9odcmsc.cloudfront.net`, API
  `https://tsd3qaxkmc.execute-api.us-east-1.amazonaws.com/prod/`.
