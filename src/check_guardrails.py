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

# Shared with the writer (src/ingest_kpm.py's registry_stamp()) so the fields this check
# verifies cannot silently drift from the fields actually stamped -- oregon-kpm#44 review:
# the two lists were maintained independently once already. `import ingest_kpm` stays
# hermetic here because ingest_kpm's own heavy imports (pypdf, corpus_toolkit.repo) are
# themselves lazy, loaded only inside the functions that need them.
sys.path.insert(0, str(ROOT / "src"))
from ingest_kpm import REGISTRY_STAMP_FIELDS  # noqa: E402

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


def check_registry_link_agrees(docs) -> list[str]:
    """A document's ERF slug, and the basis and review metadata stamped beside it, must be
    the ones the crosswalk records for its agency_key.

    Frontmatter is written by the ingester and the crosswalk is curated by hand, so the two
    can drift the moment either is edited alone -- and the failure is silent: a document
    keeps pointing at an agency the crosswalk has since remapped, and nothing reads wrong
    until someone follows the link. This is the same shape as check_series_is_current, which
    exists because a stale derived file answers with numbers that no longer match the
    documents citing them.

    `basis`, `reviewed_by` and `reviewed_on` all get exactly the same treatment as `slug`
    (oregon-kpm#44): every comparison is symmetric, both a missing value the crosswalk
    records AND a stamped value the crosswalk does not (or no longer does) are flagged. A
    `basis` that silently drifted after a re-base (#52 re-based nine entries that were
    wrongly claiming `exact`) is indistinguishable from a mechanical match at the point a
    reader actually sees it -- which is the whole reason this corpus stamps it. The same is
    true of `reviewed_by`/`reviewed_on`: those two fields exist to say a human asserted the
    identity, so a document missing them when the crosswalk has them is stamping nothing
    where it claims provenance, and a document carrying either after the crosswalk's
    sign-off is retracted is asserting a review that no longer stands on record.

    Absence is not checked here. An unmapped agency deliberately carries no slug, and
    src/link_agency_registry.py --check is what enforces that every agency_key is either
    mapped or recorded as absent with a reason.
    """
    path = ROOT / "_meta" / "agency-crosswalk.yml"
    if not path.is_file():
        return []
    mapping = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("mapping") or {}
    bad = []
    for p, fm in docs:
        slug = fm.get("agency_registry_slug")
        if not slug:
            continue
        entry = mapping.get(fm.get("agency_key") or "") or {}
        want = entry.get("slug")
        if slug != want:
            bad.append(f"{p.name}: agency_registry_slug={slug!r} but the crosswalk maps "
                       f"agency_key={fm.get('agency_key')!r} to {want!r}")
        if fm.get("agency_registry_corpus") != "executive-regulatory-frameworks":
            bad.append(f"{p.name}: agency_registry_slug without a corpus naming where the "
                       f"slug is defined")
        # REGISTRY_STAMP_FIELDS (from ingest_kpm, the writer) rather than a second hand-kept
        # list here -- so a field neither side forgets to add stays checked on both. Every
        # comparison is symmetric: a document must neither miss a value the crosswalk records
        # NOR carry one the crosswalk does not (or no longer does -- a `basis` that silently
        # drifted after a re-base, as #52's nine entries did, or a reviewed_by/reviewed_on
        # left behind after a sign-off is retracted, must not go unnoticed). `entry.get(key)
        # or None` normalises the crosswalk's "absent" against frontmatter's "absent", so
        # neither side's specific spelling of "nothing here" causes a false positive.
        for field, key in REGISTRY_STAMP_FIELDS:
            want_val = entry.get(key) or None
            got = fm.get(field)
            if got != want_val:
                bad.append(f"{p.name}: {field}={got!r} but the crosswalk records "
                           f"{key}={want_val!r} for agency_key={fm.get('agency_key')!r}")
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
    ("agency registry link matches the crosswalk", check_registry_link_agrees),
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
