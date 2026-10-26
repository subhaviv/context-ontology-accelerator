#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Flatten ASTM AnIML XML (.animl) into tabular CSVs for COA's structured grounding path.

Why this exists
---------------
COA's grounding strategy (``table_to_ontology``, the default structured path) needs a
TABULAR source — rows and columns — to ground against a target ontology. The
``KE-UniLiv/animl-ontology`` examples ship only AnIML XML (hierarchical
Sample/Category/Parameter trees) or already-mapped RDF, neither of which is tabular.
This script turns the raw ``.animl`` XML into three faithful CSV tables so COA can
induct + ground them against ``animl_ontology.owl``.

This is intentionally the "messy instrument export -> table" shape the Danaher use
case is about: the Parameter names become columns, values carry their own units in a
sibling column (the unit-conversion transformation seam), and IDs link the tables.

AnIML shape (verified against Example1 + Example3)
--------------------------------------------------
    AnIML
      SampleSet
        Sample (sampleID, name, comment)
          Category (name)
            Parameter (name, parameterType) -> <S>/<D> value
      ExperimentStepSet
        ExperimentStep (experimentStepID, name)
          Infrastructure
            SampleReferenceSet / SampleReference (sampleID, role, samplePurpose)
            Timestamp -> text
          Method (name)
            Device / Manufacturer -> text, Name -> text
          Result (name)
            Category (name)
              Parameter (name, parameterType) -> <D> value, optional <Unit label=...>

Output tables
-------------
  samples.csv          one row per Sample. Columns: sample_id, name, comment, + one
                       column per distinct Parameter name seen across sample Categories
                       (e.g. "Owner", "Mixer ID", "Initial pH"). Sparse cells blank.
  experiment_steps.csv one row per ExperimentStep: experiment_step_id, name, timestamp,
                       method_name, device_manufacturer, device_name, sample_id, sample_role,
                       sample_purpose.
  measurements.csv     one row per Result Parameter (the richest, grounding + transformation
                       seam): experiment_step_id, result_name, category_name, parameter_name,
                       parameter_type, value, unit.

Usage
-----
    python animl_flatten.py <input.animl | input_dir> [--out DIR]

    # whole example dir -> CSVs in $KIROCREW_SCRATCH/animl_csv
    python animl_flatten.py "$KIROCREW_SCRATCH/animl_examples" --out "$KIROCREW_SCRATCH/animl_csv"

Stdlib only (xml.etree, csv). Namespace-agnostic: tags are matched on local-name so it
works whether or not the file declares the AnIML XML namespace.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def _local(tag: str) -> str:
    """Local-name of a possibly namespaced tag (``{ns}Sample`` -> ``Sample``)."""
    return tag.split("}")[-1]


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    """Direct children of ``elem`` whose local-name == ``name``."""
    return [c for c in elem if _local(c.tag) == name]


def _first(elem: ET.Element | None, name: str) -> ET.Element | None:
    if elem is None:
        return None
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _text(elem: ET.Element | None) -> str:
    """Trimmed text of an element, or '' — treating AnIML's null/nan sentinels as blank."""
    if elem is None or elem.text is None:
        return ""
    t = elem.text.strip()
    return "" if t.lower() in ("null", "nan", "none") else t


def _param_value(param: ET.Element) -> str:
    """Value of a Parameter: its <S> (string) or <D> (data) child text."""
    for kind in ("S", "D", "I", "L", "F"):  # AnIML scalar value element variants
        v = _first(param, kind)
        if v is not None:
            return _text(v)
    return ""


def _param_unit(param: ET.Element) -> str:
    """Unit label of a Parameter, if any (<Unit label="µm">)."""
    unit = _first(param, "Unit")
    if unit is None:
        return ""
    # AnIML carries the human label in the @label attribute; fall back to text.
    return unit.attrib.get("label", "") or _text(unit)


def _iter_params_in_categories(container: ET.Element):
    """Yield every Parameter under any (possibly nested) Category of ``container``.

    Returns tuples of (category_name, parameter_element).
    """
    for cat in container.iter():
        if _local(cat.tag) != "Category":
            continue
        cat_name = cat.attrib.get("name", "")
        for param in _children(cat, "Parameter"):
            yield cat_name, param


def flatten_file(path: Path):
    """Parse one .animl file; return (sample_rows, step_rows, measurement_rows, sample_param_names)."""
    root = ET.parse(path).getroot()
    src = path.name

    sample_rows: list[dict] = []
    sample_param_names: list[str] = []  # order-stable union of Parameter names on samples
    step_rows: list[dict] = []
    measurement_rows: list[dict] = []

    # ── SampleSet -> Sample ────────────────────────────────────────────────
    for sample_set in (e for e in root.iter() if _local(e.tag) == "SampleSet"):
        for sample in _children(sample_set, "Sample"):
            row = {
                "sample_id": sample.attrib.get("sampleID", ""),
                "name": sample.attrib.get("name", ""),
                "comment": _text(_first(sample, "comment")) or sample.attrib.get("comment", ""),
                "_source_file": src,
            }
            # comment is sometimes an attribute, sometimes absent; keep attrib form sane
            if row["comment"].lower() in ("null", "nan", "none"):
                row["comment"] = ""
            for _cat, param in _iter_params_in_categories(sample):
                pname = param.attrib.get("name", "").strip()
                if not pname:
                    continue
                if pname not in sample_param_names:
                    sample_param_names.append(pname)
                row[pname] = _param_value(param)
            sample_rows.append(row)

    # ── ExperimentStepSet -> ExperimentStep ────────────────────────────────
    for step in (e for e in root.iter() if _local(e.tag) == "ExperimentStep"):
        infra = _first(step, "Infrastructure")
        sample_ref = None
        if infra is not None:
            ref_set = _first(infra, "SampleReferenceSet")
            sample_ref = _first(ref_set, "SampleReference") if ref_set is not None else _first(infra, "SampleReference")
        method = _first(step, "Method")
        device = _first(method, "Device") if method is not None else None

        step_id = step.attrib.get("experimentStepID", "")
        step_rows.append(
            {
                "experiment_step_id": step_id,
                "name": step.attrib.get("name", ""),
                "timestamp": _text(_first(infra, "Timestamp")) if infra is not None else "",
                "method_name": method.attrib.get("name", "") if method is not None else "",
                "device_manufacturer": _text(_first(device, "Manufacturer")) if device is not None else "",
                "device_name": _text(_first(device, "Name")) if device is not None else "",
                "sample_id": sample_ref.attrib.get("sampleID", "") if sample_ref is not None else "",
                "sample_role": sample_ref.attrib.get("role", "") if sample_ref is not None else "",
                "sample_purpose": sample_ref.attrib.get("samplePurpose", "") if sample_ref is not None else "",
                "_source_file": src,
            }
        )

        # Result Parameters -> measurements (one row per parameter; value + unit)
        for result in _children(step, "Result"):
            result_name = result.attrib.get("name", "")
            for cat_name, param in _iter_params_in_categories(result):
                pname = param.attrib.get("name", "").strip()
                if not pname:
                    continue
                measurement_rows.append(
                    {
                        "experiment_step_id": step_id,
                        "result_name": result_name,
                        "category_name": cat_name,
                        "parameter_name": pname,
                        "parameter_type": param.attrib.get("parameterType", ""),
                        "value": _param_value(param),
                        "unit": _param_unit(param),
                        "_source_file": src,
                    }
                )

    return sample_rows, step_rows, measurement_rows, sample_param_names


def _write_csv(path: Path, rows: list[dict], fixed_cols: list[str], extra_cols: list[str]) -> int:
    """Write rows to CSV with a stable header (fixed_cols + extra_cols + _source_file last)."""
    header = fixed_cols + extra_cols + ["_source_file"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return len(rows)


def main(argv: list[str]) -> int:
    """Parse args, flatten the input .animl file(s), and write the three CSV tables."""
    ap = argparse.ArgumentParser(description="Flatten AnIML XML into CSV tables for COA grounding.")
    ap.add_argument("input", help="A .animl file or a directory of them.")
    ap.add_argument("--out", default="animl_csv", help="Output directory (default ./animl_csv).")
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    files = sorted(in_path.glob("*.animl")) if in_path.is_dir() else [in_path]
    if not files:
        print(f"No .animl files found at {in_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_samples: list[dict] = []
    all_steps: list[dict] = []
    all_meas: list[dict] = []
    sample_param_union: list[str] = []

    for f in files:
        try:
            s, st, m, pnames = flatten_file(f)
        except ET.ParseError as e:
            print(f"skip {f.name}: XML parse error: {e}", file=sys.stderr)
            continue
        all_samples += s
        all_steps += st
        all_meas += m
        for p in pnames:
            if p not in sample_param_union:
                sample_param_union.append(p)

    n_s = _write_csv(out_dir / "samples.csv", all_samples, ["sample_id", "name", "comment"], sample_param_union)
    n_st = _write_csv(
        out_dir / "experiment_steps.csv",
        all_steps,
        [
            "experiment_step_id",
            "name",
            "timestamp",
            "method_name",
            "device_manufacturer",
            "device_name",
            "sample_id",
            "sample_role",
            "sample_purpose",
        ],
        [],
    )
    n_m = _write_csv(
        out_dir / "measurements.csv",
        all_meas,
        ["experiment_step_id", "result_name", "category_name", "parameter_name", "parameter_type", "value", "unit"],
        [],
    )

    print(f"flattened {len(files)} file(s) -> {out_dir}")
    print(f"  samples.csv          {n_s} rows, {3 + len(sample_param_union)} cols")
    print(f"  experiment_steps.csv {n_st} rows")
    print(f"  measurements.csv     {n_m} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
