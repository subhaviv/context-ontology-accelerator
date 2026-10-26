# AnIML eval data — target ontology + flattened source tables

Durable copies of the assets for the AnIML/AFO target-grounded run (Danaher eval Step 2).
Copied from session scratch 2026-10-26 so they survive past the session.

## Files
| File | What it is | Rows/size |
|---|---|---|
| `animl_ontology.owl` | **Target ontology** — AnIML/AFO (46 classes, 45 with definitions, 83 props). Upload as a reference ontology in Stage B. Ontology IRI: `http://www.w3id.org/animl/ontology/` | 148 KB |
| `samples.csv` | Source table 1 — one row per Sample; Parameter names → columns | 42 rows |
| `experiment_steps.csv` | Source table 2 — step, method, device, timestamp, sample ref | 63 rows |
| `measurements.csv` | Source table 3 — one Result Parameter per row; **value + unit** (the transformation seam: g, µm, rpm, %, s, °C, ms, uS/cm, m²/s, mL) | 304 rows |

## Provenance
- Ontology: `KE-UniLiv/animl-ontology` → `ontologies/animl_ontology.owl` (**CC BY 4.0** — attribute in any delivered artifact).
- CSVs: flattened from the 10 `examples/example_AnIML_files/*.animl` XML files in that repo, via `../animl_flatten.py`.

## Regenerate
```bash
# re-download the 10 AnIML XML examples, then:
python3 ../animl_flatten.py <dir-of-.animl-files> --out <out-dir>
```

## Use
See `../RUNBOOK-step2-animl.md`. Live env: coa-dev Web App https://d3upg0x9odcmsc.cloudfront.net
(agentops-publisher / 195698601974). Create a fresh namespace (e.g. `animl-afo`) — do NOT reuse
`calanthir-pharma` or `pharmacy`.
