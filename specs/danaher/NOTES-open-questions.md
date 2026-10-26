# Danaher / IDBS COA Eval — Notes: the three open questions

> Working-assumption notes, written 2026-10-23. Reasoning through the three open
> decisions in `CAPABILITY-FIT.md` *before* Danaher answers them — what the likely
> answers are, and **why each one changes the work**. These are assumptions to be
> corrected in review, not settled facts.

## 1. The prescribed target ontology

**Why it matters:** this is the single biggest fork in the eval, because it decides what
`groundingOntologyIds` points at — and therefore whether target-informed discovery
(FR-OD-05) is a one-click path or a setup project. The whole eval is *"independent
discovery vs discovery-with-a-target, and the mappings between inferred and target."*
No target → no FR-OM-* at all. The target isn't a detail; it's half the experiment.

The realistic candidates, and what each costs:

- **A built-in foundational ontology** (FIBO, Schema.org, Dublin Core, PROV-O, FOAF —
  these ship in the engine's grounding set). **Cheapest path.** But none of these is a
  *scientific/lab* ontology, and Danaher/IDBS is life-sciences R&D data (ELN/LIMS, assays,
  instruments). FIBO is finance; Schema.org is web. So a built-in is a *demonstration of
  the mechanism*, not a domain-faithful target. Fine for a mechanism proof, weak for a
  real IDBS eval.
- **A life-sciences standard we upload as a reference ontology** — the honest fit. Likely
  candidates: **SIO** (Semanticscience Integrated Ontology), **OBI** (Ontology for
  Biomedical Investigations), **ChEBI**, **AFO** (Allotrope Foundation Ontology — *the*
  lab-data standard; Allotrope is exactly the world IDBS lives in, and Danaher's instrument
  businesses SCIEX / Beckman Coulter / Molecular Devices are in that consortium). COA
  ingests uploaded OWL/Turtle/RDF, so this is supported — an upload + `CreateOntology` /
  `UploadOntology`, then use its id as the grounding target.
- **An IDBS-internal target ontology** — if IDBS has its own canonical model they're
  migrating *toward* (very plausible for a migration-planning eval), they supply it and we
  upload it the same way.

**Working assumption, absent an answer: AFO or an IDBS-internal ontology, uploaded as a
reference.** That's what makes the eval credible to IDBS domain specialists (EC-04, EC-05).
Design the target-informed run to take the target as a **pluggable uploaded reference**,
not a hardcoded built-in — so swapping FIBO-for-mechanism-demo vs AFO-for-real-eval is a
config change, not a redesign.

> *AFO note:* Allotrope Foundation Ontology = the OWL semantic layer of the Allotrope
> Framework (underpins ADF instrument-data containers). Vendor-neutral, machine-readable
> analytical lab data. Identified from general knowledge — confirm current release + OWL
> packaging + whether IDBS already uses a version before locking into a design.

## 2. The datasets, and how much is unstructured

**Why it matters:** this decides which COA code paths carry the eval, and it directly hits
the two risks flagged in `CAPABILITY-FIT.md`. The eval explicitly names (FR-DI-02) "Advanced
Spreadsheet data extracted as JSON, Word and Excel documents, and instrument data." That
mix maps onto three *very* different maturity levels in COA:

- **Relational / structured** → `table_to_ontology` + `rigor_ontology`. **Mature.** One OWL
  class per table, FKs→object properties, RIGOR generate+judge. Solid; if the eval is
  mostly structured, we're in good shape.
- **Unstructured (Word, free text)** → `/induce/unstructured/` document path. **v0 with
  documented gaps**: sparse datatype properties, no hierarchy axioms (entailment is
  pass-through), no HermiT/SHACL validation. If the eval leans on documents, the *quality
  bar* it measures against (EC-02, meaningful semantic understanding) hits exactly where
  COA is weakest.
- **Semi-structured spreadsheet-JSON / Excel / instrument data** → the **`[verify]` gap**
  and the riskiest unknown. Not clearly either path. "Advanced Spreadsheet extracted as
  JSON" is an IDBS E-WorkBook concept; whether COA's `packages/sources/` can ingest that
  shape at all is unproven. Instrument data likewise.

**Why the *proportion* matters specifically:** the eval's EC-01 (known workflow) vs EC-02
(unfamiliar/messy) are graded separately, and messy/multi-modal is where COA either shines
or exposes v0 limits. If Danaher's data is 80% relational, this is a strong eval with a
couple of caveats. If it's 80% instrument/spreadsheet/Word, half the eval runs through
immature or unverified paths and we'd be over-promising. **Working assumption: a mix with
meaningful unstructured content** (it's an IDBS R&D migration eval — their pain *is* the
messy historical ELN/instrument data), which puts the unstructured-induction v0 gaps and
the spreadsheet-ingestion `[verify]` on the critical path, not in the edge cases.

## 3. Demo-only vs a dedicated `danaher` namespace on deployed COA

**Why it matters:** this sets the entire infrastructure + governance envelope — the
difference between a days-of-work artifact exercise and a mirror of the whole Calanthir
build (namespace creation, per-namespace RRM grants, seed buckets named `coa-*`, Glue/S3
wiring, the M2M serve-agent path). The *eval itself* says production implementation is out
of scope — which argues for the lighter option.

The two shapes:

- **Demo-only / artifact-driven** — induct Danaher's bounded dataset through COA (local
  engine or a scratch namespace), produce the OWL + mapping artifacts (DO-01…DO-08),
  specialists rate them. No standing tenant, no governed serve path. **Matches the eval's
  own "out of scope: production" line.** The lighter, faster, correct-by-default reading of
  an *evaluation*.
- **Dedicated `danaher` namespace on deployed COA** (mirror Calanthir) — only needed if the
  eval must run on the *real* deployed platform with governance, or if it's really a
  pre-sales POC dressed as an eval and they want it "in the product." More infra: namespace,
  per-namespace grants (Group→role, re-granted per namespace), `coa-*` seed buckets, Glue
  tables, source registration done in the COA UI.

**Working assumption: demo/artifact-driven for the eval itself, with the namespace as a
stretch if it turns into a POC.** The requirements doc is an *evaluation*, not a deployment
— demo-only is the faithful reading. Build the Danaher assets so the *same* seed data could
later be promoted into a `danaher` namespace if it graduates to a POC, but don't stand up
the namespace speculatively.

---

## The through-line: why all three matter together

They're not independent — they compound. The eval's credibility to IDBS specialists rests
on: a **domain-faithful target** (so mappings mean something), **representative messy data**
(so EC-02 is a real test, not a toy), and a **reviewable artifact trail** (DO-*). The cheap
version of all three — built-in FIBO target, relational-only data, local demo — proves the
*mechanism* but not the *IDBS use case*. The credible version — uploaded AFO/internal
target, real multi-modal data, artifact-driven eval — is more setup but is what actually
answers "can COA help us migrate our historical data." Picking cheap on all three risks a
"nice demo, didn't test our actual problem" verdict.

## Next step if these assumptions hold

Bake them into a spec-driven `requirements.md` for the Danaher eval (numbered R*/AC*
traceable to the IDBS FR/NFR/EC ids), so the assumptions are explicit and get corrected in
review rather than argued in chat.
