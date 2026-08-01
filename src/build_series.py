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

# A KPM heading. The number may be followed by end-of-line, OR by the measure title on the
# SAME line -- 24 documents do the latter, and 10 of them put EVERY heading inline, so the
# original `[ \t]*$` found no blocks at all in them and the whole document produced no rows.
# `KPM ?#` because some drop the space (`KPM#2 Traffic Incident Management`).
#
# THE UPPERCASE LOOKAHEAD IS LOAD-BEARING, not decoration. `KPM #\d+\b` alone also matches
# PROSE that cites a measure mid-sentence, and two documents do exactly that:
#
#     appr-dlcd-2024        "KPM #14 documents how much land has been removed from ..."
#     appr-dor-2017-09-29   "KPM #1 for more details). Contingency planning for ..."
#
# Both would have opened a spurious block that swallowed the narrative following it. A real
# heading is followed by a title, which is capitalised; a citation is followed by a lowercase
# verb or preposition. Measured across all 779 snapshots: +151 headings, 24 documents, and
# zero prose matches remaining.
#
# The page-1 summary table header `KPM# Approved Key Performance Measures (KPMs)` cannot
# match either way -- there is no digit after the `#`.
# `|(?=[A-Z][a-zA-Z])` -- THE SPACE IS SOMETIMES NOT THERE AT ALL. Eight documents run the
# number straight into the title (`KPM #7PERCENTAGE OF PRIVATE FORESTLAND ...`), and
# appr-ltco-2017-09-18 does it to EVERY heading, which is why that document reports
# "no 'KPM #n' blocks" and contributes zero rows.
#
# TWO LETTERS ARE REQUIRED, not one. Five Treasury reports number their measures `KPM #2A`
# and `KPM #2B`; a one-letter lookahead reads those as KPM 2 with the title starting "A",
# merging two distinct measures onto one number. `[A-Z][a-zA-Z]` matches `7PERCENTAGE` and
# `2Average` and refuses `2A` followed by space or end of line, so those blocks stay
# unmatched -- a known gap rather than a collision.
KPM_HEAD = re.compile(r"^KPM ?# ?(\d+)(?:[ \t]*$|[ \t]+(?=[A-Z])|(?=[A-Z][a-zA-Z]))", re.M)
YEAR = re.compile(r"^((?:19|20)\d{2})$")
# The label that opens the year run. `Metric` is the SAME layout under a different word, used
# by 10 RY2016 documents (LUBA, PUC, BOLI, SoS, ODF, OPRD, OMB, OTLB, OBMI ...). The parser
# walked off the end of every one of those blocks and returned None, discarding 615 complete,
# unambiguous, full-length value runs -- not a truncation, an entire layout unread.
#
# The negative lookahead matters: these documents ALSO print `Metric Value` as a column
# heading further down, and matching that as the start of the year run would begin the run in
# the wrong place.
SERIES_HEAD = re.compile(r"^(?:Report Year|Metric(?! +Value))", re.I)
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


def align_by_column(raw_line: str, start: int, year_cols: list[float],
                    n_years: int) -> list[str] | None:
    """Assign a short value run to year columns by x-position. None if ambiguous.

    THIS IS THE ONLY SAFE WAY TO PLACE A PARTIAL ROW. pypdf emits nothing for a blank table
    cell, so `Actual 73% 73% 73%` under five years could mean the first three years or the
    last three -- and measured across 60 documents it is genuinely both: 406 runs are missing
    trailing years, 195 are missing the first four, and 52 alternate (0,2,4), the signature of
    a survey the agency runs biennially. A fill heuristic would be wrong in a large minority
    of cases, silently, attaching real numbers to the wrong years.

    REFUSING IS PART OF THE DESIGN. If any value lands more than 60% of a column pitch from
    every year, or two values claim the same year, this returns None and the run stays
    unparsed. An unparsed run is a known gap; a misaligned one is a false fact.
    """
    body = raw_line[start:]
    # ACCOUNTING PARENTHESES ARE A SIGN, AND THE VALUE PATTERN DROPS THEM. `($14.00)` would be
    # read as `$14.00` -- the same magnitude with the opposite sign, which is a false fact, not
    # a gap. One cell in the corpus does this (dsl-appr-final11-04-16 KPM #3, 2016, where the
    # narrative confirms "a corresponding deflation in FY16"), so the run is refused rather
    # than have the sign invented or silently lost.
    if re.search(r"\(\s*\$?-?[\d,]+(?:\.\d+)?%?\s*\)", body):
        return None
    got = [(m.group(), (m.start() + m.end()) / 2 + start)
           for m in re.finditer(rf"\$?-?[\d,]+(?:\.\d+)?%?|{NON_VALUE}", body, re.I)]
    if not got or len(got) > n_years:
        return None
    pitch = ((year_cols[-1] - year_cols[0]) / max(1, len(year_cols) - 1)) if year_cols else 0
    if pitch <= 0:
        return None
    out: list[str | None] = [None] * n_years
    for val, vx in got:
        dists = [abs(vx - cx) for cx in year_cols]
        j = dists.index(min(dists))
        if min(dists) > pitch * 0.6 or out[j] is not None:
            return None
        out[j] = val
    # A cell the agency left blank is NOT the same fact as a value we failed to read, and the
    # corpus already distinguishes those: `No Data` is carried as a value because the agency
    # wrote it. An empty column is recorded as null for the same reason.
    return out


# --- variant knobs -------------------------------------------------------

def count_embeddings(short, long, cap=2):
    """How many ways `short` embeds in `long` as a subsequence, capped."""
    dp = [1] + [0] * len(short)
    for v in long:
        for j in range(len(short) - 1, -1, -1):
            if short[j] == v:
                dp[j + 1] = min(cap, dp[j + 1] + dp[j])
    return dp[len(short)]


LAYOUT_GAP_HEAD = re.compile(r"^KPM ?# ?(\d+)(?:[ \t]*$|[ \t]+(?=[A-Z])|[ \t]{2,}\S|(?=[A-Z][a-zA-Z]))")


def layout_series_head(s):
    if SERIES_HEAD.match(s):
        return True
    col = re.sub(r"\s+", "", s[:20]).lower()
    return col.startswith("reportyear") or (
        col.startswith("metric") and not col.startswith("metricvalue"))


def layout_runs(path):
    """{kpm: [entry, ...]} where entry = {'a': aligned|None, 'label': str|None}."""
    if not path.is_file():
        return {}
    out = {}
    kpm, cols, n, label, cur = None, [], 0, None, None
    for raw in path.read_text().splitlines():
        s = raw.strip()
        m = LAYOUT_GAP_HEAD.match(s)
        if m:
            kpm, cols, label, cur = m.group(1), [], None, None
            continue
        if kpm is None:
            continue
        if layout_series_head(s):
            ys = list(re.finditer(r"(?:19|20)\d{2}", raw))
            cols = [(y.start() + y.end()) / 2 for y in ys]
            n = len(ys)
            label, cur = None, None
            continue
        tok = s.split()[0].rstrip(":") if s else ""
        # THE ROW LABEL IS `Actual`, CAPITAL A. Lowercase `actual` at the start of a line is
        # the chart legend (`actual   target`, 6,424 of them) or narrative prose (10), never a
        # data row: 11,329 capitalised vs 6,434 lowercase, and no counter-example either way.
        if tok == "Actual":
            e = {"label": label, "a": None, "t": None}
            if cols:
                start = re.match(r"\s*", raw).end() + len("Actual")
                e["a"] = align_by_column(raw, start, cols, n)
            out.setdefault(kpm, []).append(e)
            label, cur = None, e
            continue
        # The `Target` row belongs to the `Actual` row above it. Reading it here is what lets
        # the target carry the SAME year alignment as the actual instead of being indexed
        # positionally off a run of a different length.
        if tok == "Target":
            if cur is not None and cols:
                start = re.match(r"\s*", raw).end() + len("Target")
                cur["t"] = align_by_column(raw, start, cols, n)
            label, cur = None, None
            continue
        if tok.lower() == "target":
            label, cur = None, None
            continue
        if s and not VALUE.match(s) and len(s) < 120:
            label = s
    return out


def parse_block(lines: list[str], raw: list[str] | None = None) -> dict | None:
    """One `KPM #n` block -> {measure, dcp, years, runs:[{submeasure, actual, target}]}."""
    h = re.match(r"KPM ?# ?(\d+)[ \t]*(.*)", lines[0].strip())
    kpm_no = h.group(1)
    # THE TITLE MAY BE ON THE HEADING LINE. 24 documents print it there, and dropping it left
    # 380 rows with an EMPTY measure_key and 95 more keyed on `upward trend positive result`
    # -- the boilerplate line that follows the heading. Distinct measures then collapsed onto
    # one key, which is where `intra_document_conflicts` (asserted to be zero) got its 5.
    title, dcp, years = ([h.group(2)] if h.group(2) else []), None, []
    i = 1
    # --- header: title lines until Data Collection Period or Report Year
    while i < len(lines):
        l = lines[i].strip()
        if SERIES_HEAD.match(l) or DCP.search(l):
            break
        if l and not STOP.match(l):
            title.append(l)
        i += 1
    # --- optional data collection period (may wrap onto following lines)
    if i < len(lines) and DCP.search(lines[i]):
        d = DCP.search(lines[i]).group(1).strip()
        j = i + 1
        while j < len(lines) and not SERIES_HEAD.match(lines[j].strip()) \
                and not YEAR.match(lines[j].strip()) and lines[j].strip():
            if lines[j].strip().lower() not in LABELS:
                d += " " + lines[j].strip()
            j += 1
            if j - i > 3:
                break
        dcp = re.sub(r"\s+", " ", d).strip(" -") or None
        i = j
    # --- the year run, either inline after the label or one per line
    while i < len(lines) and not SERIES_HEAD.match(lines[i].strip()):
        i += 1
    if i >= len(lines):
        return None
    inline = re.findall(r"((?:19|20)\d{2})", lines[i])
    year_cols: list[float] = []
    if inline:
        years = inline
        # Column centres from the LAYOUT line, which preserves x-positions. Absent when the
        # caller had no layout sidecar, in which case alignment is simply not attempted.
        if raw is not None and i < len(raw):
            year_cols = [(m.start() + m.end()) / 2
                         for m in re.finditer(r"(?:19|20)\d{2}", raw[i])]
            if len(year_cols) != len(years):
                year_cols = []
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
        # `Metric Value` is a COLUMN HEADING in the Metric-layout documents, printed between
        # the year run and the first Actual. Left alone it becomes pending_label and every
        # one of those measures is filed under a submeasure the agency never named.
        if l and not VALUE.match(l) and len(l) < 120 and l.lower() != "metric value":
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
    # THE READING-ORDER TEXT REMAINS PRIMARY. Parsing the layout sidecar wholesale was tried
    # and is worse: 39,242 rows -> 28,124. It carries page furniture that `<id>.txt` has
    # stripped, and pypdf reports "Rotated text discovered. Output will be incomplete." on
    # some documents, so it loses more structure than its geometry recovers.
    #
    # Geometry is therefore consulted ONLY for runs that fail the length gate, via a lookup
    # keyed on KPM number and run ordinal. Both parses walk the same blocks of the same
    # document in the same order, so the ordinal is stable; anything that does not line up
    # falls through and the run stays unparsed, exactly as before.
    text = txt.read_text()
    lines = text.splitlines()
    aligned_runs = layout_runs(SNAPSHOTS / f"{fm['id']}.layout.txt")
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
    # Which Actual run within a KPM block we are on, so the geometry lookup addresses the
    # same run the primary parse is holding. Incremented for every run, recovered or not.
    actual_ordinal: dict[str, int] = defaultdict(int)
    recovered = 0
    bounds = [line_of(s) for s in starts] + [len(lines)]
    for b in range(len(bounds) - 1):
        blk = lines[bounds[b]:bounds[b + 1]]
        parsed = parse_block(blk)
        if not parsed:
            problems.append(f"{fm['id']} KPM block {b + 1}: no Report Year run")
            continue
        for run in parsed["runs"]:
            act, tgt = run.get("actual") or [], run.get("target") or []
            cand = aligned_runs.get(parsed["kpm_number"], [])
            ordn = actual_ordinal[parsed["kpm_number"]]
            pick = None
            if run.get("submeasure") and not str(
                    run["submeasure"]).startswith("unlabelled run "):
                want = norm_measure(run["submeasure"])
                hits = [e for e in cand if e["label"]
                        and norm_measure(e["label"]) == want]
                if len(hits) == 1:
                    pick = hits[0]
            if pick is None and ordn < len(cand):
                pick = cand[ordn]
            # THE GATE. A series whose length disagrees with the year run is misaligned,
            # and a misaligned series attaches real numbers to the wrong years -- which
            # reads as data rather than as an error. Same shape as the appropriations
            # extractor's gate, for the same reason.
            if len(act) != len(parsed["years"]):
                # SECOND CHANCE, FROM GEOMETRY. The values are short because pypdf emits
                # nothing for a blank cell, not because the measure is unreadable. If the
                # layout sidecar places this same run's values into year columns
                # unambiguously, the run is recovered with nulls where the agency printed
                # nothing. Anything ambiguous never reaches here -- align_by_column returns
                # None -- so a failure to recover leaves the run exactly as unparsed as it
                # was, never misaligned.
                ok = False
                if pick is not None and pick["a"] is not None \
                        and len(pick["a"]) == len(parsed["years"]):
                    nn = sum(x is not None for x in pick["a"])
                    if nn == len(act):
                        ok = True
                    elif len(act) == 0 and nn > 0:
                        ok = True
                    elif len(act) and nn > len(act):
                        # THE SHORT RUN MUST EMBED IN THE GEOMETRIC RUN EXACTLY ONE WAY.
                        # pypdf's reading order drops individual cells -- most often the ones
                        # printed WITHOUT decimals, so `4.10 3.90 3.70 4 3.80` arrives as four
                        # values with the bare `4` gone (appr-dcbs-2018-12-21 KPM #3, verified
                        # against the page: the 2017 cell really is printed `4`). The values it
                        # does keep stay in order, so the two runs agree iff the short one is a
                        # subsequence of the long one -- and the placement is only determined if
                        # that subsequence embeds a SINGLE way. Two embeddings means two possible
                        # year assignments, so the run stays unparsed.
                        got = [x for x in pick["a"] if x is not None]
                        n_emb = count_embeddings(act, got)
                        lab_ok = True
                        if pick["label"] and run.get("submeasure"):
                            lab_ok = (re.sub(r"[^a-z0-9]", "", pick["label"].lower())
                                      == re.sub(r"[^a-z0-9]", "",
                                                str(run["submeasure"]).lower()))
                        ok = n_emb == 1 and lab_ok
                if ok:
                    act = pick["a"]
                    recovered += 1
                else:
                    problems.append(
                        f"{fm['id']} KPM #{parsed['kpm_number']}"
                        f"{'/' + run['submeasure'] if run.get('submeasure') else ''}: "
                        f"{len(act)} actual(s) vs {len(parsed['years'])} year(s)")
                    actual_ordinal[parsed["kpm_number"]] += 1
                    continue
            actual_ordinal[parsed["kpm_number"]] += 1
            # THE TARGET NEEDS THE SAME GATE THE ACTUAL HAS. `tgt[k]` indexes a run that may
            # be SHORTER than the year run, which silently attaches a target to the wrong
            # year -- 2,193 rows in the corpus before this change. Recovered from geometry
            # where the layout places it unambiguously; refused (null) otherwise, because a
            # target on the wrong year is a false fact and a missing target is a gap.
            if len(tgt) != len(parsed["years"]):
                lt = pick["t"] if pick else None
                ok_t = False
                if lt is not None and len(lt) == len(parsed["years"]):
                    nn = [x for x in lt if x is not None]
                    ok_t = (len(nn) == len(tgt) and nn == tgt) or \
                           (not tgt and bool(nn)) or \
                           (len(tgt) and len(nn) > len(tgt)
                            and count_embeddings(tgt, nn) == 1)
                tgt = lt if ok_t else []
            for k, y in enumerate(parsed["years"]):
                # A BLANK CELL IS NOT AN OBSERVATION. Positional alignment returns a
                # full-length run with None where the agency printed nothing, which is what
                # lets the length gate pass honestly instead of being bypassed. Emitting
                # those as rows would turn "the agency did not report 2024" into a row
                # asserting a null actual for 2024 -- a different claim, and one the source
                # does not make. `No Data` still becomes a row, because the agency wrote it.
                if act[k] is None:
                    continue
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
