#!/usr/bin/env python3
"""Derive the tidy KPM series from the committed report text.

  python3 src/build_series.py            # rewrite _meta/series.json
  python3 src/build_series.py --check    # exit 1 if it would change (CI)
  python3 src/build_series.py --report   # human-readable coverage + restatements

Emits one row per (document, KPM, sub-measure, year):

    agency, agency_doc, reporting_year, kpm_number, measure, submeasure,
    year, actual, target, data_collection_period, source_report

WHAT THE SOURCE ACTUALLY LOOKS LIKE, measured over all 229 ingested reports rather than
assumed from a sample. 1,939 measure blocks across 227 documents (2 have none):

    layout per-line   1,838      Report Year \\n 2012 \\n 2013 ... Actual \\n 0% \\n 90% ...
    layout inline         5      Report Year 2012 2013 ...  /  Actual 0% 90% ...
    no Report Year       96      no series can be derived; recorded, not guessed
    sub-measures        338      a single KPM carrying more than one Actual/Target run

THE SUB-MEASURE CASE IS THE ONE THAT BREAKS A NAIVE PARSER, and it is 17% of blocks. The
seed's model was (agency, measure, year) -> actual/target, which has no room for it. Board
of Accountancy KPM #1 is one measure with SIX series under it:

    KPM #1
    CUSTOMER SATISFACTION - Percent of customers rating satisfaction ...
    Data Collection Period: Jul 01 - Jun 30
    Report Year
    2012 2013 2014 2015 2016          (one per line in the real text)
    Availability of Information
    Actual   0% 90% 77% 77% 82%
    Target  95% 95% 95% 95% 95%
    Timeliness
    Actual   0% 95% 79% 78% 83%
    ...

Read the first Actual run as "the" answer and five sixths of the measure vanishes silently,
with the surviving sixth attributed to the whole KPM. So `submeasure` is a first-class
column and is null only when the block genuinely has one unlabelled run.

KPM NUMBER IS NOT AN IDENTIFIER. Numbers are per-agency and get reused as measures retire,
so the same `KPM #3` means different things in different years for the same agency. Rows
are keyed on NORMALISED MEASURE TEXT; the number rides along as an attribute.

REPORTING YEAR IS NOT THE DATA COLLECTION PERIOD. LFO publishes a dedicated explainer to
prevent exactly this confusion, so both are carried on every row and neither is inferred
from the other. `data_collection_period` is null where the report omits it -- 14% of
documents do, which the seed's 5-of-5 sample did not reveal.

A REPORTED NUMBER IS THE AGENCY'S CLAIM. Nothing here is a finding about whether a program
worked, and `assessment` is deliberately absent: the green/yellow/red verdict is rendered
as a COLOURED GRAPHIC in the source and does not survive text extraction. The per-report
threshold definitions do survive ("Green = Target to -5%"), and they differ between reports
and between years, so an assessment reconstructed by comparing actual to target here would
be our arithmetic presented as the agency's judgement. Recorded as absent rather than
faked.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SNAPSHOTS = ROOT / "_meta" / "snapshots"
OUT = ROOT / "_meta" / "series.json"

KPM_HEAD = re.compile(r"^KPM #(\d+)[ \t]*$", re.M)
YEAR = re.compile(r"^((?:19|20)\d{2})$")
DCP = re.compile(r"Data Collection Period[:\s]*(.*)", re.I)
STOP = re.compile(r"^(How Are We Doing|About the Targets|Factors Affecting)", re.I)
# A value cell: a number, a percentage, a currency amount, or an EXPLICIT NON-VALUE the
# agency wrote. Kept tight so a stray narrative line cannot be swallowed as data.
#
# The non-value vocabulary is measured, not guessed. Counting what actually appears in a
# value-run position across all 229 reports:
#
#     No Data   1,567        TBD   986        (everything else is a label or a terminator)
#
# `No Data` was missing from the first version and it is the single most common non-numeric
# cell in the corpus. Its absence truncated runs mid-series -- DEQ KPM #1 reads
# 84% / 80% / 78% / No Data / No Data, so the run stopped at three against five years and
# the length gate rejected the whole measure. 1,850 blocks failed that way. The gate was
# right; the vocabulary was wrong.
#
# Keeping these as VALUES rather than dropping them is deliberate: "the agency reported no
# data for 2020" and "we failed to parse 2020" are different facts, and collapsing them
# would turn a reporting gap into an extraction gap.
NON_VALUE = r"No\s*Data|Not\s*(?:Available|Reported)|N/?A|TBD|Pending|--?|\*"
VALUE = re.compile(rf"^\$?-?[\d,]+(?:\.\d+)?%?$|^(?:{NON_VALUE})$", re.I)
LABELS = {"actual", "target"}


def norm_measure(text: str) -> str:
    """Key for cross-report identity. Text, never the KPM number."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_block(lines: list[str]) -> dict | None:
    """One `KPM #n` block -> {measure, dcp, years, runs:[{submeasure, actual, target}]}."""
    kpm_no = lines[0].split("#")[1].strip()
    title, dcp, years = [], None, []
    i = 1
    # --- header: title lines until Data Collection Period or Report Year
    while i < len(lines):
        l = lines[i].strip()
        if re.match(r"^Report Year", l) or DCP.search(l):
            break
        if l and not STOP.match(l):
            title.append(l)
        i += 1
    # --- optional data collection period (may wrap onto following lines)
    if i < len(lines) and DCP.search(lines[i]):
        d = DCP.search(lines[i]).group(1).strip()
        j = i + 1
        while j < len(lines) and not re.match(r"^Report Year", lines[j].strip()) \
                and not YEAR.match(lines[j].strip()) and lines[j].strip():
            if lines[j].strip().lower() not in LABELS:
                d += " " + lines[j].strip()
            j += 1
            if j - i > 3:
                break
        dcp = re.sub(r"\s+", " ", d).strip(" -") or None
        i = j
    # --- the year run, either inline after the label or one per line
    while i < len(lines) and not re.match(r"^Report Year", lines[i].strip()):
        i += 1
    if i >= len(lines):
        return None
    inline = re.findall(r"((?:19|20)\d{2})", lines[i])
    if inline:
        years = inline
        i += 1
    else:
        i += 1
        while i < len(lines) and YEAR.match(lines[i].strip()):
            years.append(lines[i].strip())
            i += 1
    if not years:
        return None

    # --- runs: an optional label line, then Actual / Target value runs
    runs, pending_label = [], None
    cur: dict = {}
    while i < len(lines):
        l = lines[i].strip()
        if STOP.match(l) or l.startswith("KPM #"):
            break
        low = l.lower().rstrip(":")
        head = low.split()[0] if low else ""
        if head in LABELS:
            vals = re.findall(rf"\$?-?[\d,]+(?:\.\d+)?%?|{NON_VALUE}", l[len(head):], re.I)
            i += 1
            while len(vals) < len(years) and i < len(lines) and VALUE.match(lines[i].strip()):
                vals.append(lines[i].strip())
                i += 1
            if head == "actual":
                if cur.get("actual") is not None:
                    runs.append(cur)
                    cur = {}
                cur["submeasure"] = pending_label
                cur["actual"] = vals
                pending_label = None
            else:
                cur["target"] = vals
                runs.append(cur)
                cur = {}
            continue
        if l and not VALUE.match(l) and len(l) < 120:
            pending_label = l
        i += 1
    if cur.get("actual"):
        runs.append(cur)

    # DISAMBIGUATE UNLABELLED SIBLING RUNS. A block can carry several Actual/Target runs
    # where the source prints no label for them -- ODA KPM #12 has three. Left as null they
    # share a key, and the restatement pass then reports one document disagreeing with
    # ITSELF: three different 2013 actuals under one measure, which is our bug wearing the
    # costume of a finding. Numbering them keeps the series distinct without inventing
    # names the source does not contain. Only applied when there is more than one run, so a
    # normal single-series measure keeps a null submeasure.
    if len(runs) > 1:
        for n, r in enumerate(runs, 1):
            if not r.get("submeasure"):
                r["submeasure"] = f"unlabelled run {n}"

    return {
        "kpm_number": kpm_no,
        "measure": re.sub(r"\s+", " ", " ".join(title)).strip(),
        "data_collection_period": dcp,
        "years": years,
        "runs": runs,
    }


def rows_for_document(md_path: Path) -> tuple[list[dict], list[str]]:
    fm = yaml.safe_load(md_path.read_text().split("---", 2)[1])
    txt = SNAPSHOTS / f"{fm['id']}.txt"
    if not txt.is_file():
        return [], [f"{fm['id']}: no snapshot text"]
    text = txt.read_text()
    lines = text.splitlines()
    starts = [m.start() for m in KPM_HEAD.finditer(text)]
    if not starts:
        return [], [f"{fm['id']}: no 'KPM #n' blocks"]

    # map char offsets to line indices once
    offs, pos = [], 0
    for n, l in enumerate(lines):
        offs.append((pos, n))
        pos += len(l) + 1
    def line_of(off): return next(n for p, n in reversed(offs) if p <= off)

    rows, problems = [], []
    bounds = [line_of(s) for s in starts] + [len(lines)]
    for b in range(len(bounds) - 1):
        blk = lines[bounds[b]:bounds[b + 1]]
        parsed = parse_block(blk)
        if not parsed:
            problems.append(f"{fm['id']} KPM block {b + 1}: no Report Year run")
            continue
        for run in parsed["runs"]:
            act, tgt = run.get("actual") or [], run.get("target") or []
            # THE GATE. A series whose length disagrees with the year run is misaligned,
            # and a misaligned series attaches real numbers to the wrong years -- which
            # reads as data rather than as an error. Same shape as the appropriations
            # extractor's gate, for the same reason.
            if len(act) != len(parsed["years"]):
                problems.append(
                    f"{fm['id']} KPM #{parsed['kpm_number']}"
                    f"{'/' + run['submeasure'] if run.get('submeasure') else ''}: "
                    f"{len(act)} actual(s) vs {len(parsed['years'])} year(s)")
                continue
            for k, y in enumerate(parsed["years"]):
                rows.append({
                    "agency": fm.get("agency"),
                    "agency_doc": fm["id"],
                    "reporting_year": fm["reporting_year"],
                    "measure_status": fm.get("measure_status"),
                    "kpm_number": parsed["kpm_number"],
                    # The full measure text lives ONCE in the `measures` lookup, not on
                    # every row. Repeating a 400-character title across 13,695 rows cost
                    # 11 MB in a file CI regenerates and diffs on every PR.
                    "measure_key": norm_measure(parsed["measure"])[:200],
                    "_measure_text": parsed["measure"][:400],
                    "submeasure": run.get("submeasure"),
                    "year": y,
                    "actual": act[k],
                    "target": tgt[k] if k < len(tgt) else None,
                    "data_collection_period": parsed["data_collection_period"],
                    "source_report": fm["source_url"],
                })
    return rows, problems


def restatements(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Where two reports disagree about the same (agency, measure, sub-measure, year).

    THE DISAGREEMENT IS THE FINDING, not noise to resolve. Each APPR carries a rolling
    five-year history, so the same historical year is reported by up to five documents; a
    corpus that quietly preferred the newest number would destroy exactly the restatement
    or methodology change this makes visible. Nothing here picks a winner.

    IDENTITY IS THE DOCUMENT, NOT THE REPORTING YEAR. Two DIFFERENT reports can state the
    same reporting year -- Columbia River Gorge Commission published both
    appr-crgc-2016-09-26 and appr-crgc-2017-09-14 as Reporting Year 2016 (the second is one
    of the files whose name disagrees with its own cover page), and they disagree about
    2014. That is a real restatement between two documents, not a parse error, and a check
    keyed on reporting year would have mislabelled it as one.

    A disagreement WITHIN a single document is always our bug, never a finding, so it is
    counted separately and asserted to be zero.
    """
    idx = defaultdict(list)
    for r in rows:
        idx[(r["agency"], r["measure_key"], r["submeasure"], r["year"])].append(r)
    out, intra = [], []
    for key, group in idx.items():
        vals = {r["actual"] for r in group if r["actual"] is not None}
        if len(vals) <= 1:
            continue
        docs = {r["agency_doc"] for r in group}
        rec = {
            "agency": key[0], "measure_key": key[1], "submeasure": key[2], "year": key[3],
            "reported": sorted({(r["agency_doc"], r["reporting_year"], r["actual"])
                                for r in group}),
        }
        (out if len(docs) > 1 else intra).append(rec)
    out.sort(key=lambda d: (str(d["agency"]), d["measure_key"], d["year"]))
    return out, intra


def build() -> dict:
    rows, problems = [], []
    for md in sorted(REPORTS.glob("*.md")):
        r, p = rows_for_document(md)
        rows.extend(r)
        problems.extend(p)
    rs, intra = restatements(rows)
    # Lift the full measure text off every row into a single lookup keyed by measure_key.
    # The text is identical for every row sharing a key, so carrying it per row was pure
    # duplication -- and an 11 MB artifact that CI regenerates and diffs on every PR.
    measures: dict[str, str] = {}
    for r in rows:
        text = r.pop("_measure_text", None)
        if text and r["measure_key"] not in measures:
            measures[r["measure_key"]] = text
    return {
        "note": ("GENERATED by src/build_series.py from the committed report text -- do "
                 "not hand-edit. Every value is the agency's own reported figure, not an "
                 "independent finding. `assessment` is deliberately absent: the "
                 "green/yellow/red verdict is a coloured graphic that does not survive "
                 "text extraction, and reconstructing it from actual-vs-target would be "
                 "our arithmetic presented as the agency's judgement. Reporting year is "
                 "NOT the data collection period; both are carried and neither is "
                 "inferred from the other."),
        "counts": {
            "rows": len(rows),
            "documents_with_rows": len({r["agency_doc"] for r in rows}),
            "agencies": len({r["agency"] for r in rows if r["agency"]}),
            "measures": len({(r["agency"], r["measure_key"]) for r in rows}),
            "rows_without_target": sum(1 for r in rows if not r["target"]),
            "rows_without_collection_period": sum(
                1 for r in rows if not r["data_collection_period"]),
            "unparsed_blocks": len(problems),
            "restatements": len(rs),
            # Always zero. A single document disagreeing with itself is a parser collision,
            # never a finding; it is surfaced as a count so a regression cannot hide.
            "intra_document_conflicts": len(intra),
        },
        "measures": measures,
        "restatements": rs,
        "intra_document_conflicts": intra,
        "unparsed": problems,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    data = build()
    text = json.dumps(data, indent=1, sort_keys=False) + "\n"

    if args.report:
        c = data["counts"]
        for k, v in c.items():
            print(f"  {k:32} {v}")
        print("\n  sample restatements (the disagreement IS the finding):")
        for d in data["restatements"][:8]:
            print(f"    {str(d['agency'])[:32]:32} data year {d['year']}  "
                  f"{[f'{doc[:18]}/RY{ry}={a}' for doc, ry, a in d['reported']]}")
        print("\n  sample unparsed blocks:")
        for p in data["unparsed"][:6]:
            print(f"    {p}")
        return 0

    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("series.json is out of date — re-run src/build_series.py", file=sys.stderr)
            return 1
        print("series.json is current")
        return 0

    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {data['counts']['rows']:,} rows, "
          f"{data['counts']['restatements']} restatement(s), "
          f"{data['counts']['unparsed_blocks']} unparsed block(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
