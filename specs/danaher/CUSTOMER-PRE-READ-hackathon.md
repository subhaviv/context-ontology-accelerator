# COA Hackathon — Pre-Read & What to Bring

**A 2-day working session: map your real source data onto your target ontology with the Context
Ontology Accelerator (COA), grade it against your own success criteria, and leave with a clear
verdict on how COA fits your mapping workflow.**

> **Please read this first — it prevents a misunderstanding.** We are **not** asking you to clean your
> data. **Bring it messy** — inconsistent units, mixed date formats, missing values, values that need
> conversion. That messiness is exactly what we want to stress-test; pristine data would prove nothing.
> The only thing we ask is **structural**: present the data as **tables with meaningful column names**,
> because that is the input contract for any structured mapper (COA included). "Messy content" and
> "un-tabular shape" are different problems — keep the mess, just give it to us as tables (and if it's
> instrument XML or a giant spreadsheet, we'll help flatten it together on Day 1).

---

## What we'll do together

Over two days we will take **your** source data and **your** target model, run them through COA, and
produce migration-ready mapping recommendations — each with a confidence score, a plain-language
rationale, and an equivalence flag ("this source field = this target field"). Then we grade the results
against the success criteria *you* define, and probe the hard cases (unit conversions, ambiguous values,
messy exports) to see where COA is strong and where a layer beside it is needed.

**This is an evaluation, not a production rollout.** The goal is evidence: does COA become the mapping
core of your workflow, and what do you build around it.

## What COA does — and where the boundary is

- **COA does the semantic mapping:** it reads your source tables, grounds them against your target
  ontology, and recommends source→target field mappings with confidence + rationale.
- **COA suggests transformations; it does not execute them.** Unit conversion (e.g. mg/L → mg/mL),
  date parsing, and inventory lookups run in the layer beside COA. COA tells you *"this column maps to
  that field and needs a unit conversion"* — the actual value math is the transformation layer's job.
- **"No match" can be a real finding,** not a failure — a source field with no target equivalent may be
  genuinely new. We'll flag these rather than force a wrong match.

## Why it's worth your time

We've already proven the full pipeline end-to-end on a public analytical-instrument dataset: COA took a
messy export, mapped it to the analytical-domain target ontology (samples, experiment steps,
measurements), produced **34 field-level mappings**, and answered a plain-English question —
*"give me each measurement's result name, value, and units"* — returning **304 governed rows** with
access controls enforced. The engine works. The hackathon stress-tests it on **your** reality.

---

## What to keep ready (the single biggest lever)

If your data arrives in good shape, **Day 1 is grading, not setup.** Please prepare:

### 1. Your target
- [ ] **The target ontology or schema** you want to map *to* (OWL/RDF, or a documented schema). This is
      essential — COA grounds against it; without it, nothing matches.

### 2. Your source data — as tables
- [ ] **Source data in tabular form** (CSV, database, or Parquet). If it's instrument XML or
      documents, we'll help flatten it to tables — flag that ahead of time.
- [ ] **One concept per table.** Each table should hold ONE kind of thing — a table of *samples*,
      a table of *measurements* — not two glued together. COA maps one table to one target concept,
      so "samples and their test results" in one wide table should be split into two tables linked by
      an ID. Each table also needs a **stable identifier column** (its primary key).
- [ ] **Meaningful column names — THIS IS THE ONE THAT MATTERS MOST.** `titer_value`, not `col_7`.
      COA matches primarily on column *names*, so a clear name is what drives a correct mapping. In
      our rehearsal we mapped 34 columns correctly on **clean names alone** — no descriptions needed.
      If your names are cryptic, rename/alias them before we run; that single step moves the needle
      more than anything else below.
- [ ] **Units in their own column** (a `value` column and a separate `unit` column), not baked into
      the value string (`"2400 rpm"`). This is where the transformation story lives and what lets the
      eval grade unit conversion.

**The following two are OPTIONAL — they *sharpen* matching but are not required. Bring them if they're
easy; don't block on them.**
- [ ] *(optional)* **Short column descriptions** — one line per column saying what it *means*. These
      are the **rescue** for columns whose names are cryptic or ambiguous; with clean names they're
      not necessary (our run used none). High value only where a name alone is unclear.
- [ ] *(optional)* **Declared relationships (foreign keys)** between tables. These let COA infer
      relationships *between* concepts (not just within a table). Useful if your success criteria care
      about cross-table links; skippable for a first mapping pass.
- [ ] **Representative rows** — avoid empty or all-null columns (they carry no signal).

### 3. How we'll grade it
- [ ] **A known-good mapping for a slice of your data** — even 3–5 fields hand-mapped by one of your
      experts. This is the answer key we score COA against. Without it we can show mappings but can't
      measure accuracy.
- [ ] **Your acceptance thresholds** — what coverage and precision count as "good enough" for you.
- [ ] **A domain expert in the room.** Only your SME can confirm a mapping is *semantically* correct for
      your domain — that judgement is yours, not ours.

### 4. The hard cases (what makes the result convincing)
- [ ] **A few "trap" examples** — a unit that needs extra context to convert, an ambiguous date format, a
      value that doesn't match its label — so we can show COA **flags or abstains** instead of guessing.
- [ ] **A couple of genuinely messy real records** — the eval *wants* messy data; it proves robustness.

### 5. Logistics to confirm before Day 1
- [ ] **Who loads the data** into the environment — your team, or us with access you provide.
- [ ] **Data sensitivity** — can the real data go into the evaluation environment, or should we use
      masked / synthetic equivalents?

---

## Roles in the room

| You (customer) | We (AWS) |
|---|---|
| Bring the data, the target, and the ground-truth judgement. Your SME decides what's semantically right. | Drive the eval, run COA, interpret results, score against your criteria. |

## One honest note
We've proven the engine on one focused dataset. The point of the hackathon is to stress it on **your**
larger, messier, real-world data — that's exactly what we're here to find out together.

---

*Questions before the session? Reach out any time. The more of the checklist above that's ready when we
start, the more of our two days we spend on results instead of setup.*
