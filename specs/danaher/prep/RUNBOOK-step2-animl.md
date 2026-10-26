# Danaher Eval Prep — Step 2 Runbook: the AnIML / Allotrope domain-faithful run

> Goal: the first **target-grounded** run — prove COA produces real source→target matches
> (`matched_class_uri` + `similarity` + `relationship`), on a Danaher-faithful domain (analytical
> chemistry, Allotrope-aligned). This is the gate the whole eval rests on; the Calanthir run never
> tested it because it had no target in the namespace.
>
> **REWRITTEN 2026-10-26** after inspecting the actual repo files + COA code (the earlier draft was
> written from web search and made two wrong assumptions — corrected below, each marked `[verified]`).
>
> **Dataset:** `KE-UniLiv/animl-ontology` (**CC BY 4.0**) — the OWL 2 formalisation of AnIML, aligned
> to the Allotrope Data Format. Ships `ontologies/animl_ontology.owl` + SHACL + examples + CQ/SPARQL.

---

## What I verified this session (facts, not assumptions)

### 1. The target ontology uploads AS-IS — no label patch needed `[verified]`
`animl_ontology.owl` parsed cleanly: **46 classes, 46 object props, 37 datatype props, 45/46 classes
carry a definition, 0/46 carry `rdfs:label`.** The missing labels were the worry. They are a **non-issue**
— COA's embed path (`packages/ontology-engine/src/coa_ontology/catalog/ingest.py`, `_text_for` /
`_class_text_for`) does:

```python
label_text = " ".join(_split_camel(lbl) for lbl in labels) or _split_camel(_local_name(str(uri)))
```

When `rdfs:label` is absent it **falls back to the URI local-name and camel-splits it** (`SeriesSet`
→ `Series Set`). Definitions are read from `rdfs:comment` AND `skos:definition` AND the OBO IAO
predicate (`_definitions`). So each class embeds as `camel-split-name + definition + synonyms` —
rich and correct. **Upload the file unmodified.**

### 2. The default structured strategy DOES ground — RIGOR does not `[verified]`
This explains the zero-match Calanthir run and matters for which path to run:
- Default structured strategy is **`table_to_ontology`** (`induce_catalog.py:243`). It runs the full
  grounding pipeline (exact-match → embedding recall → LLM rerank → tier classify) and emits
  `ConceptMatch` with `matched_class_uri` / `similarity`. **This is the path you want.**
- **`rigor_ontology` explicitly IGNORES grounding** (`rigor_ontology.py:213`: *"grounding_ontology_ids
  and grounding_mode have no effect"* — `del grounding_mode; del grounding_ontology_ids`). Do NOT
  select RIGOR for a grounded run.
- So the Calanthir run came back all-novel purely because the **candidate pool was empty** (no target
  ontology in the namespace), not because of strategy. Diagnosis holds.

### 3. Ingest WAITS for embeddings to be searchable `[verified]`
`ingest_ontology_and_wait` → `wait_for_embeddings_searchable` blocks (120 s no-progress stall timeout)
until every class/property embedding is queryable in AOSS before returning. So "confirm embeddings
populated" is built into the upload — if the import call returns (doesn't stall), the vector store is
ready to recall against. This is the step the empty Calanthir namespace never had.

### 4. The example "data" is NOT tabular — this is the real blocker `[verified]`
Inspected `KE-UniLiv/animl-ontology` file tree + file contents. There is **no CSV** anywhere. The
example data is one of two shapes:
- `examples/example_AnIML_files/*.animl` = **XML** (ASTM AnIML schema): hierarchical
  `Sample → Category → Parameter` trees, not rows/columns.
- `examples/example_ttl_files/*.ttl`, `testing/synthetic_data/*.txt` = **already-RDF instance triples**
  (`aml:sample_Basil_00002 a aml:Sample ; aml:name "Basil"`) — i.e. already mapped to the ontology.

COA's **structured** path (the grounding one, `table_to_ontology`) needs **tabular source** (CSV→Glue,
as the Calanthir run used). Neither AnIML shape is tabular. **This is the one thing to resolve before
the run** — see Stage A.

---

## The run — pick ONE Stage-A source path, then B→C→D

### Stage A — get a TABULAR source COA's grounding path can ingest
Three options, best first:

**A1 (recommended) — flatten the `.animl` XML to CSV myself.** The `Sample/Category/Parameter` tree
flattens naturally to a table: one row per Sample, columns = `sampleID, name, comment, Owner,
Mixer_ID, Status, Appearance, Initial_pH, Final_pH, ...` (the Parameter names become columns). This is
exactly the "messy instrument export → table" shape the Danaher use case is about, and it gives
`table_to_ontology` real column names to ground (`Mixer ID` → ? , `pH` → ?). I can write the flattener
(`$KIROCREW_SCRATCH`, stdlib `xml.etree`) and emit 2–3 CSVs (Sample, Container, ExperimentStep).

**A2 — build a tiny hand-made CSV from the AnIML concepts.** Faster, less faithful: a `samples.csv`
and `experiment_steps.csv` with column names drawn from the ontology's own vocabulary. Good enough to
prove grounding fires; weaker as a demo.

**A3 — use the unstructured path on the raw XML/RDF.** The old runbook's plan. Rejected for the FIRST
proof: unstructured is COA's v0 path (no hierarchy axioms, sparse datatype props, no auto-validation —
see Watch-outs) and it's a two-stage doc-ingestion→Neptune→induce pipeline. More moving parts, thinner
output. Keep it for a later "does it work on raw instrument files" test, not the gate.

> **Decision needed from you:** A1 (I write the flattener — most faithful) vs A2 (quick hand CSV). I
> recommend A1. Everything below assumes a tabular source exists in `$KIROCREW_SCRATCH`.

### Stage B — create namespace + upload the target ontology
COA UI / API (your call on who drives — you've done the COA UI steps before):
```
# namespace: animl-afo  (new, English — RIGOR/grounding require en; table_to_ontology is fine)
# upload animl_ontology.owl as a REFERENCE/foundational ontology (NOT induced)
#   → POST /ontologies/import  (file upload)  → routes through ingest_ontology_and_wait
#   → parses 46 classes + 83 props, embeds, BLOCKS until searchable, returns
# CHECK the import response:
#   embeddings.count ≈ 129 (46 classes + 83 props), status "ok"
#   mapped_class_count = 0 is EXPECTED here (no R2RML on a reference ontology)
```
If the call returns without stalling at 120 s and `embeddings.count` is non-zero → vector store
populated. **This is the exact thing the Calanthir namespace never had.**

### Stage C — register the tabular source (Stage A output) + APPROVE it
```
# CSV → S3 → Glue crawler → tables in the animl-afo namespace
# (same path as the Calanthir run; apply the coa:namespace Glue tag opt-in)
# register the datasource in COA so the tables appear in the catalog
```
**Then APPROVE the source — Model will not accept it otherwise.** Per COA's Scan flow, enrichment leaves
every asset at `PENDING_REVIEW`, and "nothing reaches Model unapproved." So after registration:
```
# the enriched source sits at PENDING_REVIEW
# approve it (Web App review screen, or the approve route → BulkReviewQueue → BulkReviewWorkerFn)
#   status must become APPROVED before induction can read the assets
```
Skipping this is a silent stall: induction reads only APPROVED assets, so an un-approved source gives the
inducer nothing to work with (looks like "no tables" / empty induction, NOT an error).

### Stage D — run induction with grounding ENABLED against the target
```
# POST /induce   (structured)
#   strategy            = "table_to_ontology"      # default; DO NOT use rigor_ontology
#   grounding_mode      = "ENHANCED"               # the knob the Calanthir run left unset
#   grounding_ontology_ids = [ <animl_ontology.owl ontology_id> ]
#   namespace           = "animl-afo"
#   ontology_uri_prefix = "http://w3id.org/animl/ontology/induced-targeted"
# poll the job to completed → pull matches.json
```

### The gate — what proves success
Open `matches.json`. SUCCESS = at least some matches with **`matched_class_uri` populated,
`similarity` a real number, `relationship` set** (not the all-null, all-"novel" Calanthir result).
Expect e.g. a `samples` table → `aml:Sample` at high similarity, `experiment_steps` → `aml:ExperimentStep`.
A sensible mix of grounded + novel is the right outcome; **all-novel again** would mean grounding still
isn't recalling (check the ontology_id passed, and that embeddings landed in Stage B).

---

## Grading (after the gate passes) — three surfaces
1. **golden-compare F1** vs `animl_ontology.owl` → `benchmark/golden_compare.py`.
2. **SHACL** — run `animl_reference_pattern.shacl` through COA's SHACL validator against the induced graph.
3. **Competency questions** — the `testing/` SPARQL suite (mapped to `requirements/` CQs) against the
   induced graph. Richest signal.

## Known gaps to set expectations (verified earlier + from engine README)
- `relationship` + `similarity` are COA-computed but **dropped at `ConceptMatch` construction**
  (`rigor_ontology.py:841/855`) on the RIGOR path — but you're on `table_to_ontology`, so confirm on
  THIS run whether they persist. If they come back null even with grounding firing, that's the 6-line
  gap-1 fix, not a grounding failure.
- Transformations (`CONVERT_UNIT`, date parse) are NOT emitted by any path — that's the eval-framework
  layer, unchanged by this run.
- Unstructured path (A3) has v0 gaps: no hierarchy axioms, sparse datatype props, no auto-validation.
  Not used in the recommended A1 path.

## License
`KE-UniLiv/animl-ontology` is **CC BY 4.0** — attribute in any delivered artifact. Ontology file
downloaded + parsed this session; example data inspected via raw.githubusercontent.

## When step 2 is "done"
`matches.json` from a `table_to_ontology` + ENHANCED run against `animl_ontology.owl` shows real
`matched_class_uri` + `similarity` (+ `relationship` if it persists) on a tabular AnIML source — the
first proof COA grounds data to a supplied target. Then the three grading surfaces give the quality read.
