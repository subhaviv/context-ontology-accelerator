# Note to COA engineering — column-level alignment axioms gap

From an AnIML/AFO target-grounded run on coa-dev (agentops-publisher / 195698601974), namespace
`animl-afo`, job `b2c50933`, strategy `table_to_ontology`, `grounding_mode=ENHANCED`, grounding ontology
`http://www.w3id.org/animl/ontology/`. All findings verified against the persisted proposal artifacts
(S3 proposals/.../latest/).

## What works
Table→class grounding is correct and well-formed. The induced ontology carries class-level SKOS
alignment axioms to the target:
```
ind:Measurements    skos:exactMatch  aml:Result
ind:ExperimentSteps skos:exactMatch  aml:ExperimentStep
ind:Samples         skos:exactMatch  aml:Sample
```
All three matched `exact`, similarity 0.92–0.95, with `relationship=exactMatch` and a real `reason`.

## The gap
**Column-level SKOS alignment axioms are not emitted.** The column second-pass in
`inducer/services/pipeline.py` (~line 640) recalls target properties per column
(`_get_candidates(..., "property", ontology_id=scoped)`) and `_pick_winner` returns a `ConceptMatch`
carrying `matched_class_uri` = the target property. But the ontology builder writes class alignment
axioms only — there is no `ind:<col> skos:exactMatch <target property>`. So a column match like
`value → aml:value` is **computed and then dropped**.

Verified by control: `ontology.ttl` has 3 alignment axioms (all class-level), 0 column-level — even
though `value`/`name`/`timestamp`/`unit` have exact target-property matches
(`aml:value`/`aml:name`/`aml:timeStamp`/`aml:hasUnits`).

## What is NOT the gap (so we don't ask for the wrong thing)
Minting `ind:` predicates in R2RML is correct and intended — the induced namespace is the proposal, Ontop
serves this customer's data on it, and the target binding belongs in alignment axioms, not in renamed
R2RML predicates. We are NOT asking to make R2RML speak the target vocabulary.

## Suggested fix (low blast radius)
Mirror the existing class-alignment emission for columns: when a column's `ConceptMatch` has
`matched_class_uri` and `match_type in (exact, high_confidence)`, emit
`<ind:col> skos:exactMatch <target property>`. R2RML, SQL, Ontop and SHACL are untouched — this only ADDS
axioms to the induced ontology. Confidence-gate it: short column names score low on embedding recall
(~0.45), so a loose gate would mis-align e.g. `device_manufacturer → aml:name`.

## Open questions for you
1. Is the absence of column alignment axioms intentional (class-only by design) or an oversight?
2. Short-token recall: `value ↔ aml:value` scores ~0.45 lexically — does property recall need tuning
   alongside axiom emission, or is the rerank expected to carry it?
3. RIGOR recalls target properties per column only as LLM RAG context, not a recorded alignment — same
   gap on that path. In scope?

## Why it matters
The Danaher/IDBS migration deliverable is field→field mapping to the target. With column alignment axioms,
that mapping is derivable from the induced ontology (the governed, SKOS way) — class grounding already
proves the mechanism; columns just need the same treatment.
