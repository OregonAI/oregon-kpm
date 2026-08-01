#!/usr/bin/env python3
"""Enforce the corpus rules that AGENTS.md states but nothing else can check.

  python3 src/check_guardrails.py            # exit 1 on any violation

WHY THIS IS A SCRIPT AND NOT SCHEMA CONFIG. `plugins.extra_schema_checks` json/yaml-parses
whole FILES against a JSON schema, which suits `_meta/catalog/*.yml` and cannot see
markdown frontmatter at all. Several rules below also compare frontmatter against the body
or against the derived series, so no per-file schema could express them regardless. Same
reasoning oregon-audits recorded.

THE RULE THEY ALL SERVE: a reported measure is THE AGENCY'S OWN CLAIM ABOUT ITSELF, not an
independent finding and not evidence a program worked. Everything here exists to stop this
corpus quietly asserting more than the agency did.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SERIES = ROOT / "_meta" / "series.json"

YEAR_SOURCES = {"document", "filename", "filename-scan", "socrata"}
STATUSES = {"approved", "proposed"}
TEXT_SOURCES = {"pdf-text", "ocr"}


def frontmatter(path: Path) -> dict:
    parts = path.read_text(encoding="utf-8", errors="replace").split("---", 2)
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}


def check_year_provenance(docs) -> list[str]:
    """A derived reporting year must never be indistinguishable from a stated one.

    Four filenames in this corpus disagree with the document they name -- APPR_ODA_2018.pdf
    is Reporting Year 2017 -- so the year and WHERE IT CAME FROM are two facts, and a
    document carrying the first without the second cannot be audited.
    """
    bad = []
    for path, fm in docs:
        if not fm.get("reporting_year"):
            bad.append(f"{path.name}: no reporting_year")
        elif fm.get("year_source") not in YEAR_SOURCES:
            bad.append(f"{path.name}: year_source={fm.get('year_source')!r} "
                       f"(must be one of {sorted(YEAR_SOURCES)})")
    return bad


def check_measure_status(docs) -> list[str]:
    """proposed vs approved must be explicit on every document.

    An APPRProposed_ file reports measures PROPOSED to the Legislature, not ones it has
    approved. For some agency-years it is the only file that exists, so it cannot be
    dropped -- and reading a proposed target as an approved one misstates what the agency
    was actually held to.
    """
    return [f"{p.name}: measure_status={fm.get('measure_status')!r}"
            for p, fm in docs if fm.get("measure_status") not in STATUSES]


def check_agency_claim_disclaimer(docs) -> list[str]:
    """Every document must say, in its own body, that the numbers are the agency's claim.

    This is the sentence that stops an agent reading a green assessment as a finding that a
    program worked. It is checked in the BODY rather than trusted to frontmatter because the
    body is what a caller is shown.
    """
    needle = "THE AGENCY'S OWN REPORT ON ITSELF"
    bad = []
    for path, _ in docs:
        body = path.read_text(encoding="utf-8", errors="replace").split("---", 2)[-1]
        if needle not in body:
            bad.append(f"{path.name}: body does not carry the agency-claim caveat")
    return bad


def check_no_invented_assessment(docs) -> list[str]:
    """The corpus must not publish a green/yellow/red verdict it computed itself.

    The real assessment is a COLOURED GRAPHIC that does not survive text extraction, and the
    thresholds behind it differ between reports ("Green = Target to -5%"). Deriving one from
    actual-vs-target would be our arithmetic wearing the agency's authority, so no document
    or series row may carry an `assessment` field. This guard exists because that field is
    the single most tempting thing to add here.
    """
    bad = [f"{p.name}: carries an assessment field" for p, fm in docs if "assessment" in fm]
    if SERIES.is_file():
        data = json.loads(SERIES.read_text())
        if any("assessment" in r for r in data.get("rows", [])[:200]):
            bad.append("series.json rows carry an assessment field")
    return bad


def check_ocr_is_declared(docs) -> list[str]:
    """OCR'd text must never be indistinguishable from text the PDF actually contained.

    Six reports in this corpus are scans with no text layer. Their bodies are a MACHINE
    READING OF AN IMAGE, and the reading is good but not clean -- the DOGAMI 2017 report
    yields "pernitted rrine sites" for "permitted mine sites". Mostly-right text is the
    dangerous case: it reads as authoritative, and a figure misread by one digit is a
    fabricated number wearing the agency's authority.

    So text_source is required on every document and the caveat is required in the BODY of
    the OCR'd ones, for the same reason check_agency_claim_disclaimer looks at the body --
    that is what a caller is actually shown. Same shape as year_source, one rule up: the
    value and where it came from are two facts, and the second is what makes the first
    auditable.
    """
    bad = []
    for path, fm in docs:
        ts = fm.get("text_source")
        if ts not in TEXT_SOURCES:
            bad.append(f"{path.name}: text_source={ts!r} "
                       f"(must be one of {sorted(TEXT_SOURCES)})")
        elif ts == "ocr":
            body = path.read_text(encoding="utf-8", errors="replace").split("---", 2)[-1]
            if "MACHINE READING OF AN IMAGE" not in body:
                bad.append(f"{path.name}: text_source is ocr but the body does not say so")
            # THE CORROBORATION MUST BE ON THE RECORD, not just performed once at ingest.
            # Condition 6 of the two-engine rule: a reader who cannot see which engines
            # agreed, by how much, and that no human checked it, has no way to weigh the
            # text -- and a later re-ingest under a weaker configuration would leave no
            # trace. `agree` and the engine names come from ocr_corroborate.notes().
            cn = fm.get("conversion_notes") or ""
            for needle, what in (("agree on", "cross-engine agreement rate"),
                                 ("dictionary-recognizable", "dictionary ratio"),
                                 ("NOT human-verified", "the NOT human-verified statement")):
                if needle not in cn:
                    bad.append(f"{path.name}: conversion_notes is missing {what}")
    return bad


def check_series_is_current(docs) -> list[str]:
    """Every document with rows in the series must still exist, and vice versa.

    A stale series answers with numbers that no longer match the documents they cite -- wrong
    in the most credible-looking way available. CI also runs build_series.py --check; this
    catches the narrower case of a series referencing a deleted document.
    """
    if not SERIES.is_file():
        return ["_meta/series.json is missing"]
    data = json.loads(SERIES.read_text())
    ids = {fm.get("id") for _, fm in docs}
    orphans = sorted({r["agency_doc"] for r in data.get("rows", [])
                      if r.get("agency_doc") not in ids})
    return [f"series.json references {len(orphans)} document(s) not in reports/: "
            f"{orphans[:5]}"] if orphans else []


CHECKS = [
    ("reporting year carries its provenance", check_year_provenance),
    ("proposed vs approved is explicit", check_measure_status),
    ("body carries the agency-claim caveat", check_agency_claim_disclaimer),
    ("no invented green/yellow/red assessment", check_no_invented_assessment),
    ("OCR'd text declares itself", check_ocr_is_declared),
    ("series matches the documents", check_series_is_current),
]


def main() -> int:
    docs = [(p, frontmatter(p)) for p in sorted(REPORTS.glob("*.md"))]
    if not docs:
        print("no documents in reports/ — nothing to check", file=sys.stderr)
        return 1
    failed = 0
    for label, fn in CHECKS:
        problems = fn(docs)
        if problems:
            failed += 1
            print(f"FAIL  {label}  ({len(problems)})", file=sys.stderr)
            for p in problems[:10]:
                print(f"        {p}", file=sys.stderr)
        else:
            print(f"ok    {label}")
    print(f"\n{len(docs)} document(s) checked, {failed} rule(s) violated.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
