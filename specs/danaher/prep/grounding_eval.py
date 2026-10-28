# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Danaher grounding eval — a thin wrapper around coa_ontology.benchmark.golden_compare.

WHY THIS EXISTS
---------------
COA's own `compare_to_golden` is the sanctioned scorecard (per-class / per-property
precision / recall / F1), and we reuse it unchanged. But it was built to score an induced
ontology that RE-DECLARES the golden's concepts under its own local names. It has two blind
spots for a *grounding* result like the Danaher/IDBS migration mapping:

  1. It matches entities by LOCAL-NAME + fuzzy string only. It does NOT follow
     ``owl:equivalentProperty`` / ``owl:equivalentClass`` edges. But a grounding run encodes
     its source->target mapping EXACTLY in those edges: the induced property
     ``ind:measurements_value`` carries ``owl:equivalentProperty aml:pointValue``. So
     `compare_to_golden` scores property F1 ~0.06 on a run that actually mapped 34 columns
     correctly — a false negative.

  2. Its recall denominator is the WHOLE golden ontology (e.g. 46 AFO classes / 83
     properties). A single source only ever touches a SUBSET (our 3-table AnIML source touches
     3 classes). Recall against the full golden (3/46 = 0.043) is the wrong denominator — it
     penalizes not reproducing concepts the source never contained.

This wrapper leaves COA source untouched and adds, BESIDE it:
  - `equivalence_matches()` — counts/lists the source->target mappings carried by
    ``owl:equivalentProperty`` / ``owl:equivalentClass`` edges (the real grounding result).
  - `subset_recall()` — recall scoped to a caller-supplied "touched subset" of golden
    entities, the denominator the eval actually cares about.
  - `score()` — runs both plus the unchanged `compare_to_golden`, returns one combined dict.

These two gaps are also the proposed enhancement to upstream `golden_compare.py`
(see specs/danaher/COA-ENG-SYNC-BRIEF.md).

USAGE
-----
    python grounding_eval.py \
        --induced path/to/ontology.ttl \
        --golden  path/to/animl_ontology.owl \
        [--subset-classes Sample,Result,ExperimentStep] \
        [--subset-props pointValue,hasUnits,name,timeStamp,identifier,quantity]

Omitting --subset-* scores against the full golden (same denominator as compare_to_golden).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import OWL, RDFS, SKOS, Graph, URIRef


def _local(uri: str) -> str:
    """Lower-cased local name (fragment or last path segment) of a URI."""
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s.lower()


def _rdf_format(path: Path) -> str:
    """Rdflib parse format from file extension (.owl/.rdf are RDF/XML)."""
    return "xml" if path.suffix.lower() in (".owl", ".rdf") else "turtle"


@dataclass
class EquivalenceMatch:
    """One source->target mapping carried by an owl:equivalent* edge."""

    induced_uri: str
    target_uri: str
    kind: str  # 'property' | 'class'
    in_golden: bool  # target resolves to an entity the golden declares


@dataclass
class GroundingScore:
    """Combined scorecard: the unchanged golden_compare dict + the equivalence view."""

    golden_compare: dict = field(default_factory=dict)
    equivalence_matches: list[EquivalenceMatch] = field(default_factory=list)
    subset_class_recall: float | None = None
    subset_property_recall: float | None = None
    subset_classes_expected: int = 0
    subset_properties_expected: int = 0

    @property
    def property_equivalences(self) -> int:
        """Count of source column -> target property equivalence edges."""
        return sum(1 for m in self.equivalence_matches if m.kind == "property")

    @property
    def class_equivalences(self) -> int:
        """Count of source table -> target class grounding edges."""
        return sum(1 for m in self.equivalence_matches if m.kind == "class")

    @property
    def property_equivalences_in_golden(self) -> int:
        """Property equivalences whose target is a golden-declared property."""
        return sum(1 for m in self.equivalence_matches if m.kind == "property" and m.in_golden)

    def as_dict(self) -> dict:
        """Serialize to a nested dict: golden_compare + equivalence + subset views."""
        return {
            "golden_compare": self.golden_compare,
            "equivalence": {
                "property_equivalences": self.property_equivalences,
                "property_equivalences_in_golden": self.property_equivalences_in_golden,
                "class_equivalences": self.class_equivalences,
                "matches": [
                    {"induced": m.induced_uri, "target": m.target_uri, "kind": m.kind, "in_golden": m.in_golden}
                    for m in self.equivalence_matches
                ],
            },
            "subset": {
                "classes_expected": self.subset_classes_expected,
                "properties_expected": self.subset_properties_expected,
                "class_recall": self.subset_class_recall,
                "property_recall": self.subset_property_recall,
            },
        }


def _golden_local_names(golden_g: Graph) -> tuple[set[str], set[str]]:
    """(class_local_names, property_local_names) declared in the golden graph."""
    classes = {_local(str(c)) for c in golden_g.subjects(object=OWL.Class) if isinstance(c, URIRef)}
    props: set[str] = set()
    for ptype in (OWL.ObjectProperty, OWL.DatatypeProperty):
        props |= {_local(str(p)) for p in golden_g.subjects(object=ptype) if isinstance(p, URIRef)}
    return classes, props


def equivalence_matches(induced_g: Graph, golden_g: Graph) -> list[EquivalenceMatch]:
    """Grounding edges in the induced graph, flagged for golden membership.

    COA expresses the two mapping kinds DIFFERENTLY, so we follow both:
      - COLUMNS -> properties: ``owl:equivalentProperty``.
      - TABLES  -> classes:    ``skos:exactMatch`` and/or ``owl:equivalentClass`` and/or a
        DIRECT NAMED ``rdfs:subClassOf`` to a class in a FOREIGN namespace (the induced
        ontology also carries rdfs:subClassOf to blank-node owl:Restrictions for cardinality,
        which are NOT groundings and are skipped because they are not URIRefs).
    """
    golden_classes, golden_props = _golden_local_names(golden_g)
    seen: set[tuple[str, str, str]] = set()
    out: list[EquivalenceMatch] = []

    def _add(s, o, kind, golden_set):
        if not isinstance(o, URIRef):  # skip blank-node restrictions
            return
        key = (str(s), str(o), kind)
        if key in seen:
            return
        seen.add(key)
        out.append(
            EquivalenceMatch(
                induced_uri=str(s),
                target_uri=str(o),
                kind=kind,
                in_golden=_local(str(o)) in golden_set,
            )
        )

    # Properties
    for s, o in induced_g.subject_objects(OWL.equivalentProperty):
        _add(s, o, "property", golden_props)

    # Classes: exactMatch + equivalentClass
    for pred in (SKOS.exactMatch, OWL.equivalentClass):
        for s, o in induced_g.subject_objects(pred):
            _add(s, o, "class", golden_classes)
    # Classes: direct NAMED rdfs:subClassOf to a foreign namespace (ignore blank restrictions
    # and same-namespace parents, which are internal hierarchy not groundings).
    for s, o in induced_g.subject_objects(RDFS.subClassOf):
        if not isinstance(o, URIRef) or not isinstance(s, URIRef):
            continue
        s_ns = str(s).rsplit("#", 1)[0].rsplit("/", 1)[0]
        o_ns = str(o).rsplit("#", 1)[0].rsplit("/", 1)[0]
        if o_ns != s_ns:
            _add(s, o, "class", golden_classes)

    return out


def subset_recall(
    equiv: list[EquivalenceMatch],
    golden_g: Graph,
    subset_classes: set[str] | None,
    subset_props: set[str] | None,
) -> tuple[float | None, float | None, int, int]:
    """Recall against the TOUCHED SUBSET of golden entities.

    An expected target (by local-name) is 'recovered' if some induced entity is
    owl:equivalent* to it. Returns (class_recall, property_recall, n_classes, n_props);
    recall is None for a dimension with no subset supplied.
    """
    hit_class = {_local(m.target_uri) for m in equiv if m.kind == "class"}
    hit_prop = {_local(m.target_uri) for m in equiv if m.kind == "property"}
    cr = pr = None
    nc = np_ = 0
    if subset_classes:
        subset_classes = {s.lower() for s in subset_classes}
        nc = len(subset_classes)
        cr = len(subset_classes & hit_class) / nc if nc else None
    if subset_props:
        subset_props = {s.lower() for s in subset_props}
        np_ = len(subset_props)
        pr = len(subset_props & hit_prop) / np_ if np_ else None
    return cr, pr, nc, np_


def score(
    induced: str | Graph | Path,
    golden: str | Graph | Path,
    subset_classes: set[str] | None = None,
    subset_props: set[str] | None = None,
) -> GroundingScore:
    """Run compare_to_golden (unchanged) + the equivalence/subset views."""
    # Import here so the module imports even if run outside the ontology-engine venv,
    # failing only when score() is actually called.
    from coa_ontology.benchmark import compare_to_golden

    def _as_graph(x, is_golden=False):
        if isinstance(x, Graph):
            return x
        p = Path(x)
        if p.exists():
            return Graph().parse(str(p), format=_rdf_format(p))
        return Graph().parse(data=str(x), format="turtle")

    induced_g = _as_graph(induced)
    golden_g = _as_graph(golden, is_golden=True)

    gc = compare_to_golden(induced_g, golden_g).as_dict()
    equiv = equivalence_matches(induced_g, golden_g)
    cr, pr, nc, np_ = subset_recall(equiv, golden_g, subset_classes, subset_props)

    return GroundingScore(
        golden_compare=gc,
        equivalence_matches=equiv,
        subset_class_recall=cr,
        subset_property_recall=pr,
        subset_classes_expected=nc,
        subset_properties_expected=np_,
    )


def _parse_csv(val: str | None) -> set[str] | None:
    if not val:
        return None
    return {t.strip() for t in val.split(",") if t.strip()}


def main() -> int:
    """CLI entry point: score an induced ontology against a golden + print JSON."""
    ap = argparse.ArgumentParser(description="Danaher grounding eval (wraps golden_compare)")
    ap.add_argument("--induced", required=True, help="induced ontology.ttl")
    ap.add_argument("--golden", required=True, help="golden ontology (.owl/.rdf/.ttl)")
    ap.add_argument("--subset-classes", help="comma-separated golden class local-names the source should touch")
    ap.add_argument("--subset-props", help="comma-separated golden property local-names the source should touch")
    args = ap.parse_args()

    s = score(
        Path(args.induced),
        Path(args.golden),
        subset_classes=_parse_csv(args.subset_classes),
        subset_props=_parse_csv(args.subset_props),
    )
    print(json.dumps(s.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
