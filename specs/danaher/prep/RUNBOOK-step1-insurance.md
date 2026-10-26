# Danaher Eval Prep — Step 1 Runbook: the Insurance benchmark

> Goal of step 1: **learn the eval mechanism on proven plumbing**, where the expected answer is
> already known. You run induction → get a proposal → golden-compare F1 → read validator findings.
> Nothing domain-specific to distract you. Step 2 (AnIML) reuses this exact flow on the real domain.
> Grounded in `packages/ontology-engine/demo/README.md` + the demo scripts. Written 2026-10-23.
>
> **Dataset:** OMG P&C insurance, 13 tables / 65 cols (Sequeda et al., arXiv 2311.07509), bundled at
> `packages/ontology-engine/demo/pc-insurance-ddl.sql` with a published golden ontology.

## What you're proving

| | Insurance (step 1) | AnIML (step 2) |
|---|---|---|
| Source input | relational DDL → tables | **AnIML XML** (not the repo's pre-transformed RDF/KG — induct from raw XML, else you grade already-semantified data) |
| Strategy | `table_to_ontology` / `rigor_ontology` (structured) | likely the **document/unstructured** path for XML — weaker v0 |
| Target ontology | the CWD/swigroup P&C golden `.ttl` | `animl_ontology.owl` (+ SHACL shapes) |
| Extra eval assets | golden-compare F1 | **+ competency questions + SPARQL suite** |

The mental model carries over; in step 2 you (a) feed raw AnIML XML not the KG, (b) may hit the
unstructured-induction v0 gaps, (c) get extra grading surfaces (CQs/SPARQL) you don't have here.

## Prereqs (one-time)

```bash
cd packages/ontology-engine/demo
pip install -r requirements-demo.txt
cp config-example.yaml config.yaml
```
Edit `config.yaml`: service URLs, `induction_defaults`, `foundational_ontologies` (Schema.org,
Dublin Core, etc.), and the Tier-2 `customer_ontologies` entry (demo uses swigroup P&C as the
ACME stand-in). Env for the engine backend (`opensearch_neptune`): `NDB_ENDPOINT`, `OSS_ENDPOINT`,
`DYNAMODB_TABLE`, `AWS_REGION` (+ Bedrock embed model). A `.env` in the package root is read first.

## Run it — two services, then the walkthrough

**Terminal 1 — mock data-catalog (:8003):**
```bash
cd packages/ontology-engine/tests/fixtures/data-catalog
uv run uvicorn app.main:app --host 0.0.0.0 --port 8003
```

**Terminal 2 — consolidated ontology-engine (:8001):**
```bash
cd packages/ontology-engine
export WORKBENCH_BACKEND=opensearch_neptune
export NDB_ENDPOINT=...  OSS_ENDPOINT=...  DYNAMODB_TABLE=...
uv run uvicorn coa_ontology.main:app --port 8001
```

**Terminal 3 — stage the insurance fixtures, then drive the demo:**
```bash
cd packages/ontology-engine
python demo/parse_ddl_to_config.py      # DDL → pc-insurance-tables.yaml (idempotent)
python demo/stage_fixtures.py           # YAML → catalog fixtures as datasource ds-pc-insurance
# restart the data-catalog service so it reloads sample_data/*.json
curl http://localhost:8003/api/v1/catalogs/ds-pc-insurance   # verify it's browsable

python demo/setup.py   # health check + load foundational/customer ontologies + embeddings
python demo/demo.py    # the induction walkthrough — pick ds-pc-insurance at Step 1
```

## What happens in `demo.py` (the flow to internalise)

1. Health check
2. **Browse data catalog** → pick `ds-pc-insurance`
3. **Browse ontology catalog** → choose grounding sources (foundational + the swigroup P&C customer tier)
4. **Induction** → set URI prefix, namespace, confidence threshold, **strategy** (`table_to_ontology`
   or `rigor_ontology`), scoring (`lexical` or `structural_fusion`); watch the pipeline
5. **Results** → color-coded match table: exact / high-confidence / ambiguous / **novel**, with
   optional top-K candidate breakdown (lexical, structural, fused scores)
6. **Validation** → HermiT consistency, taxonomy cycles, connectivity, **OntoQA** metrics, **OoPS!** pitfalls
7. **Export** → the induced ontology as Turtle
8. Timing summary

**Step 4b — the golden scorecard (the part you care about):** when a golden ontology is registered
for the datasource, the demo runs `benchmark/golden_compare.py` and prints **precision / recall / F1**
per-class and per-property, split into grounded_customer / grounded_foundational / novel. **This is
FR-OD-09 / EC-01 in action** — read how it reports, because that's the number Danaher's run produces.

## Expected metrics (reference — varies with foundational-ontology drift + LLM temperature)

| Metric | Value |
|---|---|
| Tables processed | 13 / 13 |
| Columns processed | 65 / 65 |
| Matched count | ~68 |
| Novel classes created | ~3 |
| Wall-clock (`table_to_ontology`) | ~15–30 s |

If your matched/novel counts are in this ballpark and golden-compare prints an F1, the mechanism works.

## Things to try (builds intuition for step 2)

- Grounding = `none` → everything induces as **novel** (shows what "no target" looks like → FR-OD-04).
- Lower confidence threshold (e.g. `0.5`) → more **ambiguous** matches surface.
- Try `structural_fusion` vs `lexical` → compare candidate scores.
- Run a subset of tables first to keep output readable.

## Watch-outs

- **Reproducibility:** foundational ontologies download at runtime from public URLs (Schema.org,
  etc.) and can drift, shifting metrics run-to-run. Pin `source_url` to dated snapshots in
  `config.yaml` if you need determinism (this is the NFR-3 lesson, previewed).
- Backend must be `opensearch_neptune` for Tier-2 mapped-class answerability (`na_only` no-ops the
  `coa:isMapped` marker — not relevant to golden-compare, but don't be surprised).

## When step 1 is "done"

You can: stage the dataset, run induction, read the match table, run the validators, and read the
golden-compare F1 — and explain what each number means. Then move to
`OPEN-DATASETS.md` Tier C and the AnIML step 2.
