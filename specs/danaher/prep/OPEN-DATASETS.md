# Danaher Eval — Open Datasets for Prep

> For Subhaviv's own prep/practice — NOT the delivered eval (that uses Danaher/IDBS test-system
> data, FR-DI-01). These are open datasets to rehearse the induction → golden-compare → validators
> flow on. Written 2026-10-23. Sources came from web search — **verify each repo's license and
> current availability before building on it.**

## Tier A — zero-setup, already wired into the repo (start here)

**CWD / ACME insurance benchmark** — Sequeda et al., *"A Benchmark to Understand the Role of
Knowledge Graphs on LLM Accuracy for QA on Enterprise SQL Databases"* (arXiv 2311.07509).
- **In-repo:** `packages/ontology-engine/demo/pc-insurance-ddl.sql` (OMG P&C schema, 13 tables /
  65 cols) + golden OWL at the data.world URL `benchmark/golden_compare.py`, `demo.py`,
  `scripts/live_induction_run.py` already fetch:
  `https://raw.githubusercontent.com/datadotworld/cwd-benchmark-data/main/ACME_Insurance/ontology/insurance.ttl`
- **Why:** the only dataset with BOTH a source schema AND an expected ontology, already wired into
  COA's demo + golden-compare. Practice the full two-run + F1 flow today, zero ingestion work.
- **Catch:** insurance, not life-sciences — a mechanism proof, not the Danaher story.

## Tier B — purpose-built rdb→OWL eval benchmark (closest to the task)

**`citiususc/rdb2owl-bench`** (GitHub) — "benchmarking framework for the automatic generation of
ontologies from relational databases."
- **Why:** literally the eval task — relational DB in, OWL out, with reference ontologies to score
  against. Academic analogue of the IDBS ask.
- **Catch:** verify license + domain coverage (may be generic, not lab-specific).

## Tier C — domain-faithful life-sciences (the Danaher story)

**MIMIC-IV demo → OMOP CDM** — `MIT-LCP/mimic-iv-demo-omop`, `TuftsCTSI/MIMIC2OMOP`.
- **Why:** OMOP CDM is a real documented relational standard; the **demo** subset is openly
  downloadable (full MIMIC needs PhysioNet credentialing + training — the demo does NOT). Genuine
  messy clinical data standardized to a model → exercises EC-02 (messy). Target-informed run:
  ground against OMOP's own vocabulary or an OBO target.
- **Catch:** clinical, not instrument/assay — adjacent to Danaher's lab-R&D world. Use the DEMO to
  skip credentialing.

**AnIML Ontology + Allotrope** — arXiv 2604.01728 ("The AnIML Ontology … aligns it with the
Allotrope Data Format"); plus bioassay semantification arXiv 2111.15182.
- **Why:** AnIML/AFO is EXACTLY Danaher's analytical-chemistry/instrument world — an OWL 2 ontology
  aligned to ADF. Best domain-faithful **target ontology** candidate (ties to the AFO discussion in
  `NOTES-open-questions.md`).
- **Catch:** gives a TARGET ontology, not a packaged messy SOURCE dataset — you'd still need source
  data (AnIML/ADF sample files) to induct from. More assembly.

**Biomedical ontology-matching datasets** — arXiv 2205.03447 ("ML-friendly biomedical datasets for
equivalence and subsumption ontology matching").
- **Why:** ready-made ontology↔ontology mapping ground truth → grade the mapping side (FR-OM-*)
  against known-correct mappings (FR-OM-08).

## Suggested staging

1. **CWD insurance (Tier A)** — prove the two-run + golden-compare + validators flow end-to-end,
   zero ingestion friction.
2. **MIMIC-IV-demo → OMOP (Tier C)** — true relational standard, messy clinical data, no
   credentialing on the demo, scored against OMOP/OBO.
3. **Hold AnIML/AFO** as the domain-faithful target to show Danaher their actual world — but it's
   "target ontology + assemble source," not turnkey.

## Honesty caveats
- Web-search sourced: verify each repo's **license and current availability** (esp. rdb2owl-bench
  and the arXiv-linked artifacts).
- None is a perfect Danaher instrument-data match — the ideal source is Danaher's own (FR-DI-01).
  These are for PRACTICE, not the delivered eval.
