# Note to COA engineering — column→property alignment never clears the confidence gate

From an AnIML/AFO target-grounded run on coa-dev (agentops-publisher / 195698601974), namespace
`animl-afo`, job `b2c50933`, strategy `table_to_ontology`, `grounding_mode=ENHANCED`, grounding ontology
`http://www.w3id.org/animl/ontology/`. Verified against the persisted proposal artifacts and the code.

## What works
Table→class grounding is correct: `ind:Samples/Measurements/ExperimentSteps skos:exactMatch` the AnIML
classes, `exact`, similarity 0.92–0.95, with `relationship` and `reason` populated.

## The gap (verified end-to-end, not just from output)
Column→target-property alignment is **already implemented and confidence-gated** — the builder emits
`owl:equivalentProperty` when a column match is `exact`/`high_confidence`
(`inducer/strategies/table_to_ontology.py:787-792`). **It just never fired:** `ontology.ttl` has
`owl:equivalentProperty` count = 0, and `matchConfidence` appears only on the 3 classes, none on any
column. So every column classified below `high_confidence`.

Root cause is **recall/scoring, not emission or a missing feature:**
- Column recall is pure cosine, with **no exact-name shortcut**. The TABLE path has an exact-name→1.0
  shortcut; the COLUMN path (`pipeline.py` `_get_candidates`→`_search_one`→`_cosine_similarity`) does not.
- `_classify` bands: `≥0.95` exact, `≥0.80` high_confidence, `≥0.50` ambiguous, else novel.
- AnIML columns recalled ~0.44–0.46 — even `value`↔`aml:value` (identical names) — so they classified
  `novel`, and the existing alignment gate correctly skipped them.

**The structural asymmetry (root cause):** table grounding is a 3-layer pipeline — (1) embedding recall,
(2) exact-name check (`_exact_name_matches`), (3) LLM rerank (Claude Haiku judges, returns confidence +
relationship). That is why `samples→aml:Sample` scored 0.95: the reranker recognized the match and
overrode the weak raw cosine. Column matching (`_pick_winner`) is **layer 1 only** — no exact-name check,
no rerank — so a column's score IS its raw cosine (~0.45) and nothing lifts an identical-name match.

So `value` never aligned to `aml:value` because a 1-word column embeds weakly against a property whose
embedding text is a long definition, and — unlike tables — there is no exact-name boost and no reranker to
rescue it.

## What is NOT the gap
- NOT missing emission code — it exists at `:787`.
- NOT R2RML — minting `ind:` predicates is correct; the target binding belongs in the ontology alignment
  axioms, which Ontop/SQL don't touch.
- NOT the confidence gate being wrong — a 0.45 match SHOULD be novel; the issue is that an exact-name
  match only scores 0.45 in the first place.

## Suggested fix (small, in recall — not the builder)
Give the column path an exact/normalized-name shortcut mirroring the table path: when a source column
name normalizes equal to a target property's label/local-name, boost that candidate (to exact), so it
clears the gate and the existing `:787` code emits `owl:equivalentProperty`. No builder/R2RML/Ontop change.

## Open questions for you
1. Was the column path's lack of an exact-name shortcut (vs. the table path having one) intentional?
2. Beyond exact-name, is embedding-text for short columns worth enriching (e.g. include table + column
   description in the column's embed text) to lift genuine-but-not-identical matches?
3. RIGOR recalls target properties per column only as LLM RAG context — same no-shortcut weakness. In scope?

## Why it matters
The Danaher/IDBS deliverable is field→field mapping to the target. The machinery to express it
(`owl:equivalentProperty`, confidence-gated) is already there; columns just aren't scoring high enough to
trigger it. A name-match boost would light up the field-level mappings the use case grades.
