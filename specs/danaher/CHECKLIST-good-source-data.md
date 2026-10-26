# Danaher / IDBS COA Eval — Initial Checklist: what "good source data" looks like for induction

> Context: the customer plans to **iterate** — induct, inspect the inferred ontology, go
> back and improve the source, take notes, repeat — so they build their own definition of
> "what good looks like for source documents." This checklist is the starting point for
> those notes. Written 2026-10-23.
>
> **Grounding:** every item below is tied to what COA's induction *actually consumes*, read
> from `external-docs/content/ontologies.md`, `sources.md`, `custom-connector-sources.md`,
> and `packages/ontology-engine/README.md`. This is not generic data-quality advice — it is
> "what makes COA induction produce a faithful ontology," because that is the customer's own
> success signal.

## How induction reads a source (so the checklist makes sense)

COA has two structured strategies and a document path. They key off different things:

- **`table_to_ontology`** (default): embeds **column names**, vector-matches them against
  grounding-ontology classes/properties. One OWL **class per table**; **columns → datatype
  properties**; **foreign keys → object properties**. → *Names and keys carry everything.*
- **`rigor_ontology`** (RIGOR gen-LLM + judge-LLM): walks tables in **FK topological order**,
  feeds related existing concepts + the table's metadata as context to write/refine a Turtle
  fragment. → *Metadata text and FK structure drive quality.*
- **Document induction** (`unstructured_lexical_graph`): builds OWL from a lexical graph of
  entities/facts/topics. → *v0, weaker; see `CAPABILITY-FIT.md` risks.*

Across all of them, induction reads **whatever the catalog says at the moment it runs** —
descriptions, synonyms, glossary terms, tags, and declared keys. So "good source data" =
**good data + good metadata, curated *before* the source is approved.**

> ⚠️ **The timing rule that bites first** (`ontologies.md`): a metadata edit made *after* a
> source is approved is saved and shown, but **does not reach the next induction**. Curate
> descriptions/synonyms **while the source is still in review**, then approve, then induce.
> This is the #1 note the customer will want in their loop.

---

## The checklist

### A. Structure & naming (drives `table_to_ontology` matching)

- [ ] **Tables map to real concepts, one concept per table.** One OWL class is minted per
      table. A junction/bridge table becomes a class too — expected, but know it going in.
- [ ] **Column names are semantic, not cryptic.** Column *names* are embedded and matched to
      the target ontology. `lead_time_days` matches; `col_7` / `val` / `f_ldt` match nothing.
      Rename or alias opaque columns before induction.
- [ ] **Table/column names are consistent** across the dataset (same concept spelled the same
      way) so related tables converge on the same class instead of proliferating duplicates.
- [ ] **A stable identifier column per table** (acts as the primary key / class instance key).

### B. Keys & relationships (drives object properties + RIGOR's FK ordering)

- [ ] **Primary keys are declared.** From `information_schema` for DB sources; via `@pk` in
      the column comment for custom-connector sources. No PK → weaker instance identity.
- [ ] **Foreign keys are declared, not just implied.** FKs become `owl:ObjectProperty`
      relationships — this is how the ontology gets its *edges*. A dataset with real
      referential structure but **no declared FKs** induces as a bag of disconnected classes.
      - DB/Unity Catalog: declared constraints come across automatically.
      - Custom connector: encode `@fk(parent_table.parent_column)` in the column comment.
- [ ] **FK parents are in scope.** A foreign key whose parent table lies *outside* the
      exposed schema/source is **dropped** — the relationship silently won't appear. Keep both
      ends of a relationship in the same source/scope.
- [ ] **Relationship columns aren't buried in free text.** A material→site link encoded as a
      comma-joined string in one cell is not a key COA can read; model it as a real FK/junction.

### C. Metadata & curation (drives RIGOR, grounding, and semantic match)

- [ ] **Column descriptions exist and say what the column *means*** (not restate its name).
      These are per-column comments in the Arrow schema / DB comments; RIGOR and grounding both
      read them. A good description is the single biggest lever on `rigor_ontology` quality.
- [ ] **Synonyms / glossary terms added for domain jargon**, so an induced class aligns to the
      target even when the source name differs from the standard's name.
- [ ] **Curate while the source is in review** (see the timing rule above) — not after approve.
- [ ] **Units and datatypes are explicit and valid** (`xsd:dateTime`, not `datetime`;
      `xsd:string`, not `xsd:varchar`). Malformed datatype tokens surface as a Validate warning;
      clean ones avoid a repair step and make downstream comparison faithful.

### D. Content quality (drives EC-01/EC-02 grading — "meaningful" vs "messy")

- [ ] **Representative rows present** — RIGOR/LLM steps sample content; empty or near-empty
      tables give the model nothing to reason from.
- [ ] **No all-null / single-value columns** masquerading as attributes (they induce as
      meaningless properties).
- [ ] **Consistent encoding of the same fact** (one date format, one unit convention, one
      categorical vocabulary) so a column reads as one concept, not several.
- [ ] **Messy is allowed — but *note* it.** The eval *wants* messy data (FR-DI-03, EC-02). The
      customer's loop is exactly: observe what the mess did to the ontology, decide whether to
      clean the source or accept the limitation, and record which.

### E. Unstructured / instrument sources (weakest path — set expectations)

- [ ] **Facts carry an explicit object, not an inline value string.** Document induction v0
      emits datatype properties only from facts with a real `__OBJECT__` edge; ~84% inline-value
      facts on the dev graph produced *zero* datatype properties. Well-structured extraction →
      richer ontology.
- [ ] **Don't expect hierarchy from documents yet.** v0 entailment is pass-through: no
      `rdfs:subClassOf` axioms from the doc path. Hierarchy must come from structured sources or
      the target ontology via grounding.
- [ ] **Spreadsheet-JSON / Excel / instrument ingestion is unverified** (`[verify]` in
      `CAPABILITY-FIT.md`). Before relying on it, confirm `packages/sources/` actually ingests
      that shape.

---

## How to turn this into the customer's "good looks like" notes

For each induction iteration, record:

1. **What changed in the source** (renamed columns, added FK, wrote descriptions, fixed units).
2. **What changed in the inferred ontology** (new edges appeared, a class stopped duplicating,
   a mapping confidence rose, a novel class became grounded).
3. **The rule learned** ("columns named `*_id` with a declared `@fk` reliably become object
   properties"; "tables with no description induce as bare classes with no `rdfs:comment`").

That running 1→2→3 log *is* the deliverable the customer is after — an empirical, COA-specific
definition of good source data, traceable to observed induction outcomes rather than asserted up
front. It also feeds FR-OD-08 (traceability) and NFR-02 (evaluation traceability) directly.

## Still to verify before locking this
- `[verify]` spreadsheet-JSON / Excel / instrument ingestion in `packages/sources/` (Section E).
- Exact grounding-confidence signal exposed per mapping (affects B/C "confidence rose" note).
