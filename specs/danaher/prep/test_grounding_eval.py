# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: D101, D102, D205 — test classes/methods are self-documenting
"""Tests for the Danaher grounding-eval wrapper (specs/danaher/prep/grounding_eval.py).

Proves the two things plain `compare_to_golden` cannot measure on a grounding result:
  1. source->target mappings carried by owl:equivalentProperty/equivalentClass are counted,
  2. recall can be scoped to the touched subset of the golden (the eval's real denominator).

Pure inline fixtures — no S3, no network. Modeled on the AnIML run shape
(ind:measurements_value owl:equivalentProperty aml:pointValue, etc.).

Run from the ontology-engine package so `coa_ontology.benchmark` imports:
    cd packages/ontology-engine && ../../.venv/bin/pytest \
        ../../specs/danaher/prep/test_grounding_eval.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the sibling module by path (it lives under specs/, not an installed package).
_MOD_PATH = Path(__file__).with_name("grounding_eval.py")
_spec = importlib.util.spec_from_file_location("grounding_eval", _MOD_PATH)
assert _spec and _spec.loader
grounding_eval = importlib.util.module_from_spec(_spec)
sys.modules["grounding_eval"] = grounding_eval  # so @dataclass can resolve the module
_spec.loader.exec_module(grounding_eval)

pytestmark = pytest.mark.unit


# A tiny AnIML-AFO-shaped golden: the target properties our columns map to.
GOLDEN = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix aml: <http://www.w3id.org/animl/ontology/> .

aml:Sample a owl:Class ; rdfs:label "Sample" .
aml:Result a owl:Class ; rdfs:label "Result" .
aml:ExperimentStep a owl:Class ; rdfs:label "Experiment Step" .
aml:SeriesSet a owl:Class ; rdfs:label "Series Set" .

aml:pointValue a owl:DatatypeProperty ; rdfs:label "point value" .
aml:hasUnits a owl:DatatypeProperty ; rdfs:label "has units" .
aml:name a owl:DatatypeProperty ; rdfs:label "name" .
aml:identifier a owl:DatatypeProperty ; rdfs:label "identifier" .
aml:timeStamp a owl:DatatypeProperty ; rdfs:label "time stamp" .
"""

# Induced ontology in the GROUNDING shape: induced local names (ind:*) whose mapping to the
# target is carried by owl:equivalent* edges — exactly what compare_to_golden cannot see.
INDUCED = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ind: <http://coa.amazon.com/ns/induced#> .
@prefix aml: <http://www.w3id.org/animl/ontology/> .

ind:samples a owl:Class ; skos:exactMatch aml:Sample ; rdfs:subClassOf aml:Sample .
ind:measurements a owl:Class ; skos:exactMatch aml:Result ; rdfs:subClassOf aml:Result .

ind:measurements_value a owl:DatatypeProperty ; owl:equivalentProperty aml:pointValue .
ind:measurements_unit  a owl:DatatypeProperty ; owl:equivalentProperty aml:hasUnits .
ind:samples_name       a owl:DatatypeProperty ; owl:equivalentProperty aml:name .
ind:samples_lotNumber  a owl:DatatypeProperty ; owl:equivalentProperty aml:identifier .
ind:samples_created    a owl:DatatypeProperty ; owl:equivalentProperty aml:timeStamp .
"""


class TestEquivalenceCounted:
    def test_property_equivalences_counted(self):
        s = grounding_eval.score(INDUCED, GOLDEN)
        # 5 equivalentProperty edges, all to golden-declared aml: properties.
        assert s.property_equivalences == 5
        assert s.property_equivalences_in_golden == 5

    def test_class_equivalences_counted(self):
        s = grounding_eval.score(INDUCED, GOLDEN)
        assert s.class_equivalences == 2

    def test_equivalence_target_not_in_golden_flagged(self):
        induced = (
            INDUCED
            + "\nind:samples_mystery a owl:DatatypeProperty ; "
            + "owl:equivalentProperty <http://example.com/other#foo> .\n"
        )
        s = grounding_eval.score(induced, GOLDEN)
        assert s.property_equivalences == 6
        # the bogus target is not a golden property
        assert s.property_equivalences_in_golden == 5

    def test_golden_compare_alone_misses_them(self):
        """Control: the unchanged compare_to_golden scores property recall ~0 on this input,
        because the matches live in edges it does not traverse. This is WHY the wrapper exists.
        """
        s = grounding_eval.score(INDUCED, GOLDEN)
        # compare_to_golden sees ind:* local names, not aml:* — most matches are missed.
        # (A few identical-named props like aml:name may still fuzzy-align, so this is <=, not <.)
        assert s.golden_compare["property"]["recall"] <= 0.2
        # but the wrapper recovers the real result
        assert s.property_equivalences_in_golden == 5


class TestSubsetRecall:
    def test_subset_recall_right_denominator(self):
        """The source touches 3 classes / 5 properties of the golden — recall should be
        measured against THAT subset, not the full golden (which also has SeriesSet).
        """
        s = grounding_eval.score(
            INDUCED,
            GOLDEN,
            subset_classes={"Sample", "Result", "ExperimentStep"},
            subset_props={"pointValue", "hasUnits", "name", "identifier", "timeStamp"},
        )
        # 2 of 3 expected classes mapped (ExperimentStep absent from this fixture)
        assert s.subset_classes_expected == 3
        assert s.subset_class_recall == pytest.approx(2 / 3)
        # 5 of 5 expected properties mapped
        assert s.subset_properties_expected == 5
        assert s.subset_property_recall == pytest.approx(1.0)

    def test_no_subset_leaves_recall_none(self):
        s = grounding_eval.score(INDUCED, GOLDEN)
        assert s.subset_class_recall is None
        assert s.subset_property_recall is None


class TestSerialization:
    def test_as_dict_shape(self):
        s = grounding_eval.score(
            INDUCED, GOLDEN, subset_props={"pointValue", "hasUnits", "name", "identifier", "timeStamp"}
        )
        d = s.as_dict()
        assert set(d.keys()) == {"golden_compare", "equivalence", "subset"}
        assert d["equivalence"]["property_equivalences_in_golden"] == 5
        assert d["subset"]["property_recall"] == pytest.approx(1.0)
        assert len(d["equivalence"]["matches"]) == 7  # 5 props + 2 classes
