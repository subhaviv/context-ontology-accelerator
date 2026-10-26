# Danaher / IDBS — November Onsite Run-of-Show (2-day COA migration eval)

> The customer-facing working session where IDBS brings their real source data + target, and we run it
> through COA, grade it against their own success criteria, and leave with a shared component-vs-replacement
> verdict. Distinct from the internal COA-eng sync (see `COA-ENG-SYNC-BRIEF.md`). Written 2026-10-26.
>
> **North star (whiteboard this):** *Run IDBS's actual source→target migration data through COA, grade it
> against their success criteria, and leave with an evidence-based verdict on whether COA replaces or slots
> into their AI Mapper.*
>
> Grounds on: the diagnosed "empty candidate pool → all-novel" failure mode, the `table_to_ontology` +
> ENHANCED grounding path, the AnIML rehearsal assets (`prep/animl_flatten.py`, `prep/RUNBOOK-step2-animl.md`,
> 3 CSVs), and the component-vs-replacement workflow (`coa-mapper-workflow.html`).

---

## Roles in the room
| Who | Owns |
|---|---|
| **AWS (you)** | Drives the eval, owns the narrative, interprets results, runs the scorer. |
| **COA engineers** | Debug live, land small fixes on the spot, answer "can COA do X" authoritatively. |
| **IDBS / customer SMEs** | Bring data + target + domain judgement — only they can say a mapping is *semantically right*. |

> You CANNOT grade semantic correctness for their domain. Their SMEs are the ground-truth judges for
> "is `Titer_Value → Result_Value` actually correct." Build that into the agenda, don't assume it.

---

## PHASE 0 — BEFORE the customer arrives (the week prior) — THE MOST IMPORTANT PHASE
The onsite fails if setup eats Day 1. Every item here is done and verified beforehand.

- [ ] **AnIML rehearsal passed end-to-end.** Run upload-ontology → Glue → induce (`table_to_ontology`,
      `grounding_mode=ENHANCED`, `grounding_ontology_ids=[http://www.w3id.org/animl/ontology/]`) →
      `matches.json` shows **non-null** `matched_class_uri` + `similarity`. This proves the pipeline works
      on *known-good* data, so any weak result on customer data is clearly THEIR-data-specific, not a COA break.
- [ ] **1a fix landed** (`relationship` + `reason` wired through `pipeline.py:475`) so the output is already
      migration-usable — the equivalence flag + rationale present — when the customer first sees it.
- [ ] **Eval harness built + tested on AnIML.** Reads `matches.json` + ground-truth tables → scorecard
      (coverage / precision / negative-test flagging / repeatability). Pointed at AnIML now; re-pointed at
      IDBS data on the day. (Transform engine is the "Mapper stand-in" inside the harness — deterministic
      UCUM/`pint`, date parse, stubbed lookup — labelled as NOT-COA.)
- [ ] **IDBS data + target obtained early IF POSSIBLE.** The use case PDF says real data is "provided
      separately (3-5 examples)." If even a sample lands before the onsite, Day 1 becomes grading not
      plumbing. CHASE THIS — it is the single biggest schedule lever.
- [ ] **Environment confirmed** — namespace creation, Glue/source registration, induce access in the COA
      env, with a named owner. Default strategy confirmed = `table_to_ontology` (NOT `rigor_ontology`,
      which ignores grounding).
- [ ] **Failure-mode playbook ready** — if the first customer run is all-novel: check (1) target ontology
      actually in the namespace, (2) its class embeddings populated (import returned, didn't stall 120s),
      (3) `grounding_ontology_ids` passed + correct, (4) strategy is `table_to_ontology`. This is the exact
      Calanthir failure — recognizable and fixable in minutes WITH this checklist.

---

## DAY 1 — Ingest + first grounded run on THEIR data

**Morning — setup on real data (fast, because Phase 0 de-risked it)**
- Load IDBS's **target ontology/schema** into a fresh namespace; confirm embeddings populated.
- Load IDBS's **source tables** (their real, messy exports). If not already tabular, flatten (the AnIML
  flattener is the template for an instrument-export flattener).
- Register the source via Glue; confirm tables in the catalog.

**Afternoon — the first grounded run (the moment of truth)**
- Induce: `table_to_ontology` + `grounding_mode=ENHANCED` + `grounding_ontology_ids=[<their target>]`.
- **Open `matches.json` together.** Walk the matches with their SMEs: which source fields grounded to
  which target concepts, at what confidence, with what rationale; what came back novel.
- If weak/all-novel → run the Phase-0 failure-mode playbook live (don't let it read as "COA failed").

**End of Day 1 outcome:** the customer has *seen COA map their own data*. That alone reframes the
engagement from "will it work" to "how good is it."

---

## DAY 2 — Grade, probe the hard cases, and the verdict

**Morning — grade against THEIR success criteria**
- Run the harness → **scorecard**: coverage (≥80% target attrs with a recommendation ≥60% conf),
  precision (zero wrong at ≥90% conf), SME-acceptance tally.
- **Negative tests live** — feed the trap rows (unit needing molecular weight → flag-don't-guess;
  ambiguous date; label-vs-value mismatch). Show COA/harness correctly **flags or abstains** rather than
  bluffing. Target: 100% flagged.
- **Repeatability** — 3 runs, same pairs, confidence within 10 points (needs seed/temperature pinning —
  verify in Phase 0).

**Afternoon — the boundary and the gaps, made concrete**
- **Shape sensitivity** — show how their export shape changes grounding quality (unit inline vs. split,
  wide vs. long). Hand them "shape your input like this for COA" guidance — a deliverable they keep.
- **The transformation boundary, demonstrated** — walk `coa-mapper-workflow.html`: COA maps (stage ②),
  the transform/validate/lookup layer (stage ③, THEIRS) executes. Show the harness computing
  `2400 mg/L → 2.4 mg/mL` OUTSIDE COA. This *shows* component-vs-replacement instead of asserting it.

**Close — the verdict + roadmap**
- **Component-vs-replacement answer**, backed by two days of runs: COA = the semantic-mapping brain
  (match + confidence + rationale + equivalence); the transform/validate/lookup layer stays IDBS's.
- **Gap list with sizing** — what COA does today, what the small fixes (1a done, model-stamp) buy, what
  needs the layer beside it.
- **Next steps** — does IDBS adopt COA as the mapping core of their Mapper, and what do they build around it.

---

## The scorecard (what you fill in live on Day 2)
| Criterion | Target | COA result | Notes |
|---|---|---|---|
| Coverage | ≥80% target attrs, rec ≥60% conf | | |
| Precision | 0 wrong at ≥90% conf | | SME-judged |
| Negative tests | 100% flagged/abstained | | the trap rows |
| Repeatability | 3 runs within 10 conf pts | | needs seed pinning |
| Traceability | model version on each rec | | the model-stamp fix |
| Equivalence | skos:exactMatch present | | the 1a fix |

---

## The single biggest risk, and the kill
**Risk:** the first run on their data comes back weak/all-novel and reads as "COA failed" — when it's a
setup gap (target not embedded, wrong strategy, bad shape), exactly the Calanthir failure.
**Kill:** (1) the AnIML rehearsal proves the pipeline works, so a weak result is demonstrably
their-data-specific; (2) the Phase-0 failure-mode playbook turns a day-derailing mystery into a
few-minute fix; (3) frame up front that "novel" can mean "independent discovery," not "failure" — set
that expectation before the first run, not after.

## Out of scope for the onsite (say so, don't drift)
- Building the full transformation/validation/lookup engine — weeks, and it's explicitly NOT COA.
- Production deployment / migration at scale — this is an eval, not an implementation.
- Rewriting COA — only the small, located fixes (1a, model-stamp) are in play.

## Assets this onsite reuses (all in `specs/danaher/`)
- `prep/animl_flatten.py` — instrument-export → CSV flattener (template for IDBS exports).
- `prep/RUNBOOK-step2-animl.md` — the step-by-step grounded run (the rehearsal, and the template for the live run).
- `coa-mapper-workflow.html` — the component-vs-replacement diagram to screen-share.
- `COA-ENG-SYNC-BRIEF.md` — the field fixes + decisions settled with COA eng beforehand.
- Harness (to build) — the scorer + Mapper-stand-in.
