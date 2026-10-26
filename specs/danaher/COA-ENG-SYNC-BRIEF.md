# COA Engineering Sync — Briefing (Danaher / IDBS source-to-target migration eval)

> For the meeting with the **AWS COA engineering team**. Audience owns the code, so this is framed as
> located-in-code change requests + decisions, not vague asks. Written 2026-10-26, grounded in a read of
> `packages/ontology-engine` this session. File:line references are from that read — confirm against HEAD.

## One-line framing
We're validating whether COA can ground messy source data against a **supplied target ontology** and
produce **migration-ready mapping recommendations** for a Danaher/IDBS source→target migration use case,
and agreeing where COA is the right tool vs. where a layer beside it is needed.

## Where we are (evidence we bring)
- **Diagnosed a prior all-novel run.** A structured Calanthir run returned all-"novel" because the
  namespace had **no target ontology** to recall candidates against — not a matching failure. Confirmed
  against live data (only an `induced#` ontology in the namespace; empty candidate pool).
- **Target assessed + source built for the real test.** `animl_ontology.owl` (AnIML/Allotrope — Danaher's
  instrument domain) parses clean: 46 classes, 45/46 with definitions. Built a tabular source from real
  Unilever AnIML XML via a flattener (`specs/danaher/prep/animl_flatten.py`): 3 CSVs — 42 samples / 63
  experiment steps / 304 measurements. The measurements table carries value+unit (`3.395 g`, `47.9 µm`,
  `2400 rpm`) — the exact unit-conversion seam the use case grades.
- **Run is staged** (`specs/danaher/prep/RUNBOOK-step2-animl.md`): upload ontology → register CSVs via
  Glue → induce with `table_to_ontology` + `grounding_mode=ENHANCED` → inspect `matches.json`.

## Code facts we verified (confirm these are current)
1. **`table_to_ontology` grounds; `rigor_ontology` does NOT.** `rigor_ontology.py:~232` does
   `del grounding_mode; del grounding_ontology_ids` — grounding params are no-ops on RIGOR. Default
   structured strategy is `table_to_ontology` (`schemas.py` `InductionRequest.strategy` default). Confirm
   this is the deployed default.
2. **`ConceptMatch` already HAS the fields** `relationship`, `reason`, `source_column`
   (`inducer/schemas.py:48`). The grounding SERVICE populates them (`grounding.py:620`
   `relationship="exactMatch"`, `reason=...`). So the schema is fine — the gaps are at specific
   construction sites and one missing field (below).

---

## ASK 1 — the field additions (the "few fields" — located precisely)

### 1a. Persist `relationship` + `reason` at the structured SELECT site
`pipeline.py:455` and `:475` (`_select_best_candidate`, the `table_to_ontology` path) build `ConceptMatch`
**without** `relationship` or `reason`, even though the grounding reranker computed them. Result: the
migration recommendation loses the **equivalence flag** (`skos:exactMatch` ⇒ the "these are the same field"
signal the use case requires) and the **rationale**.
- **Change:** thread the reranker's `relationship` and `reason` through `_select_best_candidate` into the
  `ConceptMatch(...)`. The fields already exist; this is wiring, not a schema change. ~Small.
- **Also RIGOR** `rigor_ontology.py:841` / `:855` omit them too — lower priority since RIGOR doesn't ground,
  but same one-line-per-site fix for consistency.
- **Confirm on a live grounded run** whether these are already null in `matches.json`; if so this is the fix.

### 1b. NEW field: `transformation` on `ConceptMatch`
The use case needs the mapping to carry a transformation expression (`CONVERT_UNIT(Titer_Value, "mg/mL")`,
`PARSE_DATE(...)`). **No such field exists today.**
- **Change:** add `transformation: str | None = None` to `ConceptMatch` (`schemas.py:48`), and extend the
  ENHANCED reranker's JSON contract + prompt (`grounding.py`) to emit a suggested transformation string.
- **Scope boundary (decision, see Ask 2):** COA emitting the *suggested* expression is cheap. COA computing
  the *correct converted value* (deterministic UCUM/unit math) is NOT COA's job — that's the layer beside it.
  This field makes COA a transformation-*suggester*, which is the right amount for COA to own.

### 1c. NEW field(s): model-version + repeatability stamp
The use case's Traceability criterion wants each recommendation to carry **the model version that produced
it**; repeatability wants runs comparable. `ConceptMatch` has `scoring_strategy` but no model id/version.
- **Change:** stamp the grounding/rerank **model id + version** on the match (or the `InductionReport`).
  Small. Confirm whether `scoring_strategy="grounding_enhanced"` + job config already carries enough, or a
  dedicated field is wanted.

### 1d. (Raise, don't block) validation-rule + glossary-export fields
Lower priority, flag as direction: a `validation_rules` field on the recommendation (permitted values /
ranges / type checks) and an **export of approved mappings as glossary entries**. COA has SHACL on the
structured path (ontology-shape validation) but not per-mapping data-validation rules in the recommendation.

---

## ASK 2 — decisions only the COA eng team can make
1. **Transformation/validation/lookup layer — roadmap or out of scope?** Unit conversion (`mg/L→mg/mL`),
   date parsing, inventory-API lookups. Is any of this on COA's roadmap, or explicitly the customer's layer?
   This decides the "component vs. replacement" answer for the customer.
2. **Do you agree COA's role = ingest + induce (mapping + confidence + rationale + equivalence)**, with
   transform/validate/lookup as a layer beside it (Glue/Mapper)? We're building an eval framework on that
   premise.
3. **Is there an official eval methodology** beyond `benchmark/golden_compare.py` + the SHACL/HermiT/OntoQA
   validators? We don't want to build a parallel harness if one exists.
4. **The `matched_count` vs `matches.json` discrepancy** we saw on a prior job record (7 vs 13-all-novel) —
   which is authoritative, and is `matched_count` counting something different?

## ASK 3 — access to run the grounded test (the unblocker)
Everything is staged except the live run, which needs the COA control plane:
- Who runs upload-ontology + Glue source-registration + induce in `coa-dev-*` — us (with access) or you
  (we supply CSVs + ontology + induce params)?
- Confirm the environment and a named owner. This is the single thing gating the first target-grounded result.

## The gate we're trying to reach
`matches.json` from a `table_to_ontology` + ENHANCED run against `animl_ontology.owl` showing non-null
`matched_class_uri` + `similarity` (+ `relationship` once 1a lands) on the AnIML CSVs — the first proof COA
grounds real data to a supplied target. Then golden-compare F1 / SHACL / competency-question SPARQL give the
quality read.

## Field-change summary (for a PR checklist)
| # | Field | Where | Type | Effort |
|---|---|---|---|---|
| 1a | populate `relationship`, `reason` | `pipeline.py:455/475` (+ `rigor_ontology.py:841/855`) | wiring | small |
| 1b | add `transformation` | `schemas.py:48` + `grounding.py` reranker contract/prompt | schema + prompt | ~1–2d |
| 1c | add model id/version stamp | `schemas.py` ConceptMatch / InductionReport + grounding call | schema + wiring | small |
| 1d | `validation_rules` + glossary export | schema + emit + post-approval export | direction | larger |
