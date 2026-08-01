#!/usr/bin/env python3
"""Ingest Annual Performance Progress Reports from _meta/source-manifest.yml.

  python3 src/ingest_kpm.py --years 2016,2017,2018     # the validated first slice
  python3 src/ingest_kpm.py --only appr-boa-2016-10-07
  python3 src/ingest_kpm.py --limit 5 --refetch

THE DOCUMENT IS THE AUTHORITY ON ITS OWN REPORTING YEAR, NOT ITS FILENAME.

Every APPR states `Reporting Year <YYYY>` on page 1, and this ingester reads it there.
That is not belt-and-braces; the filenames genuinely cannot be trusted:

  * `OBD Annual Performance Progress Report.pdf` carries no year at all. It is Reporting
    Year 2019.
  * `Updated 2016 APPR_CFB_2017-16-02.pdf` names two different years.
  * The same agency published `OBD 2018 Annual Performance Progress Report.pdf` WITH a
    year and the file above WITHOUT one, in the same convention.

So `reporting_year` comes from the text, `year_source: document` records that it did, and
any disagreement with the manifest's filename-derived guess is reported rather than
silently resolved. A disagreement is a finding about the upstream naming, not noise.

THE FILENAME IS NOT THE AGENCY EITHER. `OBD` is the Board of DENTISTRY. `OBDD` is the
Business Development Department. Two agencies, near-identical codes, and the only reliable
statement of which is which is the agency name printed on page 1. Socrata's clean agency
name is used to cross-check where it has one.

EXTRACTION IS NOT UNIFORM WITHIN A SINGLE REPORT. Measured on the Board of Dentistry's
2019 report, the same data appears in two layouts on adjacent pages:

    page 3:  Report Year 2015 2016 2017 2018 2019
             Actual 100% 100% 100% 100% 100%      <- values inline after the label

    page 4:  Report Year                          <- values one per line
             2015
             2016
             Actual
             12
             11

A series parser written against either layout silently returns nothing on the other. Stage
3 must handle both and reconcile series length against the year run; this stage only
records the text and asserts the anchors are present, so a Stage 3 failure cannot be
mistaken for a fetch problem.

PDF text extraction also inserts spurious intra-word spaces ("Annual P erform ance P
rogress R eport"), so anchor matching normalises whitespace before comparing and falls
back to a space-stripped comparison.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from corpus_toolkit.repo import hash_snapshot           # noqa: E402

MANIFEST = ROOT / "_meta" / "source-manifest.yml"
SNAPSHOTS = ROOT / "_meta" / "snapshots"
OUT_DIR = ROOT / "reports"

UA = "OregonAI-corpus-platform/1.0 (+https://github.com/OregonAI/oregon-kpm)"

# Anchors the seed verified present in 5 of 5 sampled reports, re-verified here. Their
# ABSENCE is what distinguishes a scanned image from a text PDF, so it is checked at
# ingestion rather than discovered by Stage 3 as a mysterious empty series.
ANCHORS = ["KPM #", "Report Year", "Actual", "Target", "Data Collection Period"]

REPORTING_YEAR = re.compile(r"Reporting\s+Year\s*[:\-]?\s*((?:19|20)\d{2})", re.I)
REPORTING_YEAR_TIGHT = re.compile(r"ReportingYear[:\-]?((?:19|20)\d{2})", re.I)


def slug(text: str) -> str:
    """Filename -> a legal document id.

    The schema requires ^[a-z0-9][a-z0-9-]+$ and the file stem must equal the id, but
    upstream names carry spaces, capitals, parentheses and at least one typo
    ("ODAg KPM Reprot.pdf"). The mapping is recorded in `source_filename` so the id can
    always be traced back to the file it came from.
    """
    s = re.sub(r"\.pdf$", "", text, flags=re.I).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-{2,}", "-", s) or "untitled"


def fetch_pdf(url: str, dest: Path, refetch: bool) -> None:
    if dest.is_file() and not refetch:
        return
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
    if not body.startswith(b"%PDF"):
        # Stage 1 verified with HEAD, which proves the headers say PDF and not that the
        # body is one. This is where that check lands, exactly as documented there.
        raise ValueError(f"not a PDF despite its content-type ({len(body)} bytes)")
    dest.write_bytes(body)


def page_furniture(pages: list[list[str]]) -> tuple[set[str], set[str]]:
    """Lines repeated at the top/bottom of most pages: letterhead, banners, footers.

    Same threshold as oregon-audits and oregon-records-retention -- a line is furniture
    only if it appears on more than half the pages, which keeps a genuine repeated heading
    in a short report from being stripped as chrome. Shared behaviour, not a third
    implementation.
    """
    if len(pages) < 4:
        return set(), set()
    top, bot = {}, {}
    for p in pages:
        for l in [x.strip() for x in p[:3] if x.strip()]:
            top[l] = top.get(l, 0) + 1
        for l in [x.strip() for x in p[-3:] if x.strip()]:
            bot[l] = bot.get(l, 0) + 1
    half = len(pages) / 2
    return ({l for l, n in top.items() if n > half},
            {l for l, n in bot.items() if n > half})


def is_page_number(line: str, npages: int) -> bool:
    s = line.strip()
    return bool(re.fullmatch(r"(page\s+)?\d{1,3}(\s*(of|/)\s*\d{1,3})?", s, re.I)) and \
        len(s) <= 16 and npages > 1


def looks_letter_spaced(text: str) -> bool:
    """Did the extractor put a space between EVERY character?

        'A n n u a l  P e r f o r m a n c e  P r o g r e s s  R e p o r t'

    Four Long-Term Care Ombudsman reports come out of pypdf like this. They are not scans
    and not corrupt -- the text layer is complete and the anchors are all present under the
    space-stripped comparison, so ingestion accepted them. Stage 3 could not read one line:
    `Report Year` never matches, the year run never starts, and all four documents
    contributed zero rows to the series while looking perfectly healthy.

    Collapsing the spaces in the string is NOT a fix. Word boundaries use the same single
    space as letter boundaries, so 'L o n g T e r m' collapses to 'LongTerm' and
    '2 0 1 9 2 0 2 0' to '20192020' -- the years would have to be re-split by guessing.
    Re-extracting with a different engine recovers the real spacing instead of inventing it.
    """
    toks = text.split()
    if len(toks) < 50:
        return False
    return sum(1 for t in toks if len(t) == 1) / len(toks) > 0.6


def pdftotext_pages(pdf_path: Path) -> list[str] | None:
    """Poppler's extractor, used ONLY as a fallback. Returns None if it is unavailable.

    `-layout` IS REQUIRED, not a preference. Plain `pdftotext` emits this table in column
    order rather than reading order, interleaving the legend into the middle of it:

        actual / Report Year / target / 2021 / 2022 / 2023 / 2024 / 2025

    so the year run starts on `target`, finds no year, and the block parses to nothing --
    the same zero-row outcome as the letter-spacing it was called in to fix. `-layout`
    preserves x-positions, which puts the legend on its own line and the years inline after
    `Report Year`, the layout Stage 3 already reads.
    """
    try:
        r = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                           capture_output=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").split("\f")


def extract_text(pdf_path: Path) -> tuple[str, str]:
    """Returns (cleaned full text, raw first-page text).

    The first page is returned separately and UNCLEANED because the agency name and
    reporting year live there, and page-furniture stripping can remove a cover line that
    happens to repeat.
    """
    reader = PdfReader(str(pdf_path))
    raw_pages = [(p.extract_text() or "") for p in reader.pages]

    # Fall back to poppler when pypdf letter-spaces the whole document, and ONLY then --
    # pypdf stays the extractor of record for all 779, so this cannot quietly change the
    # text of documents that were already fine. Verified on the fallback's own output: if
    # poppler is missing or letter-spaces it too, keep pypdf's text and let Stage 3 report
    # the block as unparsed rather than substitute an untested extraction.
    if looks_letter_spaced("\n".join(raw_pages)):
        alt = pdftotext_pages(pdf_path)
        if alt and not looks_letter_spaced("\n".join(alt)):
            raw_pages = alt
    pages = [t.splitlines() for t in raw_pages]
    head, foot = page_furniture(pages)
    out: list[str] = []
    for lines in pages:
        for l in lines:
            s = l.strip()
            if not s or s in head or s in foot or is_page_number(s, len(pages)):
                continue
            out.append(s)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

    # A LINE OF PDF TEXT THAT STARTS WITH '## ' TRUNCATES THE DOCUMENT.
    # corpus_toolkit.repo.FULLTEXT_RE reads the section as ^## Full text\s*$(.*?)(?=^## |\Z),
    # so the first '## ' at column zero ends it and the rest of the body is silently lost
    # while the section still looks complete. oregon-audits lost half of six reports to
    # exactly this. One leading space defeats the anchor, and normalize_ws() erases it
    # before any comparison, so the in-order check and source_sha256 are unaffected.
    text = re.sub(r"^(#{1,6}\s)", r" \1", text, flags=re.M)
    return text, (raw_pages[0] if raw_pages else "")


def loose(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def find_reporting_year(first_page: str, full_text: str) -> str | None:
    """The field of record. Page 1 first; fall back to the body, then to space-stripping."""
    for hay in (first_page, full_text[:4000]):
        m = REPORTING_YEAR.search(loose(hay))
        if m:
            return m.group(1)
    for hay in (first_page, full_text[:4000]):
        m = REPORTING_YEAR_TIGHT.search(re.sub(r"\s+", "", hay))
        if m:
            return m.group(1)
    return None


def find_agency(first_page: str, socrata_name: str | None) -> tuple[str, str]:
    """(agency name, where it came from). Socrata's name is clean; page 1 is authoritative.

    Returns the page-1 line when there is one, because the filename code cannot be trusted
    (OBD is Dentistry, OBDD is Business Development) and Socrata only covers 2016-2018.
    """
    for raw in first_page.splitlines():
        line = loose(raw).strip()
        if not line or REPORTING_YEAR.search(line):
            continue
        if re.search(r"annual\s*p?\s*erform", line, re.I):   # the title line, spacing-tolerant
            continue
        if len(line) > 3:
            return line, "document"
    if socrata_name:
        return socrata_name, "socrata"
    return "", "unknown"


def build_document(src: dict, text: str, sha: str, year: str, agency: str,
                   agency_source: str, missing_anchors: list[str],
                   year_disagrees: str | None) -> str:
    rid = src["_id"]
    status = src.get("measure_status") or "approved"
    code = src.get("agency_code")
    citation = f"APPR {code or slug(agency)[:24].upper()} {year}"

    notes = []
    if missing_anchors:
        notes.append("missing expected anchors: " + ", ".join(missing_anchors))
    if year_disagrees:
        notes.append(f"filename implied reporting year {year_disagrees}; the document "
                     f"states {year} and the document wins")
    if agency_source != "document":
        notes.append(f"agency name taken from {agency_source}, not the document's own "
                     f"cover page")

    fm = {
        "schema_version": 1,
        "corpus": "oregon-kpm",
        "jurisdiction": "oregon",
        "id": rid,
        "title": f"{agency or 'Unnamed agency'} — Annual Performance Progress Report, "
                 f"Reporting Year {year}",
        "doc_type": "performance_report",
        "citation": citation,
        "authority_level": "agency_report",
        "issuing_body": agency or "Unnamed agency",
        "agency": agency or None,
        "agency_code": code,
        # From the DOCUMENT, per the module docstring. year_source says so explicitly so a
        # reader never has to guess whether a year was stated or inferred.
        "reporting_year": year,
        "year_source": "document",
        "filename_year": src.get("reporting_year"),
        # NOT a boolean and NOT a doc_type split. An APPRProposed_ file reports measures
        # PROPOSED to the Legislature rather than ones it has approved; for some
        # agency-years it is the only file that exists. Reading a proposed target as an
        # approved one is a wrong answer about what the agency was actually held to.
        "measure_status": status,
        "source_url": src["url"],
        "source_filename": src["filename"],
        "source_format": "pdf",
        "retrieved": time.strftime("%Y-%m-%d"),
        "source_sha256": sha,
        "status": "current",
        "content_mode": "verbatim",
        # 789 source PDFs at ~1.5 MB each is over a gigabyte, and the toolkit's supported
        # way to say "we did not commit the binary" is this. Nothing is weakened: with a
        # committed .txt, source_sha256 still verifies against that text and the full-text
        # coverage check still runs. What is lost is the archival copy, and that is a real
        # cost here -- 41 manifest URLs already 404.
        "snapshot_policy": "hash-only",
        "conversion_notes": "; ".join(notes),
        "maintainer": "@morficflux",
        # Written EMPTY on purpose: the schema requires both keys and a human sets them at
        # PR approval. An ingester that stamps a verification it did not perform is worse
        # than a blank.
        "last_verified": "",
        "verified_by": "",
        "relationships": {"implements": [], "implemented_by": [],
                          "references_external": [], "related": [], "supersedes": []},
        "tags": ["kpm", "performance", f"ry{year}"],
    }
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100).rstrip()

    parts = [f"---\n{head}\n---\n", "## At a glance\n"]
    parts.append(
        f"**{citation}** — {agency or 'Unnamed agency'}, Reporting Year {year}\n\n"
        f"- Agency: {agency or 'not stated'}\n"
        f"- Reporting year: {year} (stated in the document)\n"
        f"- Measures reported as: {'proposed to the Legislature' if status == 'proposed' else 'legislatively approved'}\n"
        f"- Source file: `{src['filename']}`\n\n"
        # NOT a blockquote: `>` is reserved for text quoted FROM the report, and rendering
        # our own caveat that way would make it read as the agency's words.
        "_NON-AUTHORITATIVE copy. Every target, actual and green/yellow/red assessment "
        "below is THE AGENCY'S OWN REPORT ON ITSELF, not an independent finding and not a "
        "statement that a program worked. The reporting year is not the data collection "
        "period; read each measure's stated period before attaching a number to a year. "
        "Verify at the source URL._\n")
    parts.append("\n## Full text\n\n" + text + "\n")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", help="comma-separated reporting years to ingest")
    ap.add_argument("--only", metavar="DOC_ID")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()

    sources = yaml.safe_load(MANIFEST.read_text())["sources"]
    for s in sources:
        s["_id"] = slug(s["filename"])

    if args.years:
        want = {y.strip() for y in args.years.split(",")}
        # Filter on the manifest's filename-derived year. Documents whose year is unknown
        # until the PDF is read are INCLUDED so they can be resolved rather than skipped
        # forever by a filter that depends on the thing being resolved.
        sources = [s for s in sources
                   if str(s.get("reporting_year")) in want or not s.get("reporting_year")]
    if args.only:
        sources = [s for s in sources if s["_id"] == args.only] or sys.exit(
            f"no manifest source with id {args.only!r}")
    if args.limit:
        sources = sources[:args.limit]

    OUT_DIR.mkdir(exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    ok = failed = skipped = 0
    disagreements: list[str] = []

    for i, src in enumerate(sources, 1):
        rid = src["_id"]
        try:
            pdf = SNAPSHOTS / f"{rid}.pdf"
            fresh = not pdf.is_file() or args.refetch
            fetch_pdf(src["url"], pdf, args.refetch)
            text, first_page = extract_text(pdf)
            if len(text) < 500:
                raise ValueError(f"only {len(text)} chars extracted — scanned or broken PDF")

            year = find_reporting_year(first_page, text)
            if not year:
                skipped += 1
                print(f"  [{i}/{len(sources)}] {rid}  SKIPPED: no 'Reporting Year' in the "
                      f"document and none derivable", file=sys.stderr)
                continue

            fy = src.get("reporting_year")
            disagrees = str(fy) if fy and str(fy) != year else None
            if disagrees:
                disagreements.append(f"{src['filename']}: filename {fy} vs document {year}")

            agency, agency_source = find_agency(first_page, src.get("agency"))
            flat = loose(text)
            nospace = re.sub(r"\s+", "", text)
            missing = [a for a in ANCHORS
                       if a not in flat and re.sub(r"\s+", "", a) not in nospace]

            (SNAPSHOTS / f"{rid}.txt").write_text(text, encoding="utf-8")
            sha = hash_snapshot(rid, "pdf", SNAPSHOTS)
            (OUT_DIR / f"{rid}.md").write_text(
                build_document(src, text, sha, year, agency, agency_source, missing,
                               disagrees),
                encoding="utf-8")
            ok += 1
            flag = "  ANCHORS:" + ",".join(missing) if missing else ""
            print(f"  [{i}/{len(sources)}] {rid}  RY{year}  {len(text):>7,} chars{flag}")
            if fresh:
                time.sleep(1.5)        # polite: this host serves at ~120 KB/s
        except Exception as e:                      # noqa: BLE001 — reported, not hidden
            failed += 1
            print(f"  [{i}/{len(sources)}] {rid}  FAILED: {type(e).__name__}: {e}",
                  file=sys.stderr)

    print(f"\n{ok} ingested, {skipped} skipped, {failed} failed.")
    if disagreements:
        print(f"\n{len(disagreements)} filename/document year disagreement(s) — the "
              f"document won in every case:")
        for d in disagreements[:20]:
            print(f"  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
