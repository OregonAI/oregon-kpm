#!/usr/bin/env python3
"""Discover every Annual Performance Progress Report and write _meta/source-manifest.yml.

  python3 src/enumerate_kpm.py            # rewrite the manifest
  python3 src/enumerate_kpm.py --check    # exit 1 if the manifest would change (CI)

WHERE THE DATA COMES FROM, because the obvious path is closed and the working one is not
the page a human would guess.

`oregonlegislature.gov/lfo/Pages/KPM.aspx` is public and renders an "Explore & Search"
web part over the `/lfo/APPR` document library. The LIBRARY ITSELF IS CLOSED to anonymous
callers. Measured 2026-07-31, all 401:

    /lfo/APPR                                          401
    /lfo/APPR/Forms/AllItems.aspx                      401
    /lfo/_api/web/lists(guid'...')/items               401
    /lfo/_api/web/GetFolderByServerRelativeUrl(...)    401
    /lfo/_vti_bin/listdata.svc/APPR                    401
    /lfo/_vti_bin/owssvr.dll?Cmd=Display&List=...      401

INDIVIDUAL FILES UNDER IT ARE PUBLIC. `/lfo/APPR/APPR_BOA_2016-10-07.pdf` returns 200 and
969,682 bytes of real PDF. So the problem is not access, it is ENUMERATION: we can read
any report whose filename we know, and cannot list the directory to learn the names.

Two sources supply names, and both are needed:

  1. SharePoint's search RSS endpoint, `/_layouts/15/srchrss.aspx?k=<query>&start=<n>`,
     which IS anonymous (200) even though every list API above is not. This is the only
     LFO-side enumeration that works. `_api/search/query` is not an alternative: it
     answers 500 `SafeQueryPropertiesTemplateUrl ... is not a valid URL` for every query
     — a server-side misconfiguration, not an auth failure, and not something we can fix
     from outside.

  2. Socrata dataset `kvbx-erfw` on data.oregon.gov, which carries 238 rows for 2016-2018
     ONLY (79/79/80, 80 distinct agencies) with an exact `report_document.filename` and a
     clean agency name per row. It is a document index and holds no measure data, but as
     a NAME source for those three years it is exact where search is best-effort.

NEVER CONSTRUCT A FILENAME. At least six conventions are in live use, found by sweeping:

    APPR_BOA_2016-10-07.pdf                          APPR_<CODE>_<YYYY-MM-DD>
    APPR_OST_2023.pdf                                APPR_<CODE>_<YYYY>
    APPR_OPRD_2022_08_15.pdf                         underscore date, not hyphen
    APPRProposed_DSL_2024-10-09.pdf                  proposed rather than approved
    OBD 2018 Annual Performance Progress Report.pdf  agency-first prose, spaces
    Annual Performance Progress Report - 2019 (LCIS).pdf   agency in trailing parens

The embedded date is a publication date and is not derivable from anything. A sweep that
guessed `APPR_<CODE>_<YYYY-MM-DD>` across a plausible date range would need ~136,000
requests against a state web server and would still miss every prose-named file. Discover,
verify, and record — do not generate.

TWO TRAPS IN THE RSS OUTPUT, both silent:

  * Links come back as `http://` with UNENCODED SPACES. Fetching them verbatim fails in a
    way that looks exactly like link rot. Normalise to https and percent-encode the path.
  * A single query saturates: `k=APPR` still returns rows at start=601 and returns none at
    start=1001, so one query cannot be assumed complete. We sweep several query terms AND
    partition by year, then union — a term that saturates in one partition will not in
    another.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "_meta" / "source-manifest.yml"
SWEEP_CACHE = ROOT / "_meta" / ".cache" / "swept-urls.txt"

RSS = "https://www.oregonlegislature.gov/_layouts/15/srchrss.aspx"
SOCRATA = "https://data.oregon.gov/resource/kvbx-erfw.json"
LIBRARY = "/lfo/APPR/"
INDEX_PAGE = "https://www.oregonlegislature.gov/lfo/Pages/KPM.aspx"

# The reporting years the corpus attempts. 2013 is included because the KPM page lists it;
# 2014 and 2015 are NOT listed there and are expected to come back empty. An empty year is
# recorded as a gap with a reason, never dropped.
YEARS = [2013] + list(range(2016, 2026))

QUERY_TERMS = ["APPR", "Annual Performance Progress Report", "Key Performance Measure"]

UA = "OregonAI-corpus-platform/1.0 (+https://github.com/OregonAI/oregon-kpm)"
PAGE = 10          # srchrss returns 10 rows per page and ignores requests for more
MAX_START = 1000   # empirically the ceiling: rows at start=601, none at start=1001
SLEEP = 0.3        # be a polite guest on a state web server


def get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def normalise(link: str) -> str:
    """RSS emits http:// with raw spaces. Both must be fixed or the fetch 404s."""
    link = link.strip()
    if link.startswith("http://"):
        link = "https://" + link[len("http://"):]
    parts = urllib.parse.urlsplit(link)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), parts.query, "")
    )


def sweep_rss() -> set[str]:
    """Union of every /lfo/APPR/ URL the search feed will admit to."""
    found: set[str] = set()
    # Bare terms, plus each term partitioned by year. The partition is what keeps a
    # saturating query from hiding results — see the module docstring.
    queries = list(QUERY_TERMS) + [f"{t} {y}" for t in QUERY_TERMS for y in YEARS]
    for q in queries:
        before = len(found)
        for start in range(1, MAX_START + 1, PAGE):
            url = f"{RSS}?k={urllib.parse.quote(q)}&start={start}"
            try:
                xml = get(url).decode("utf-8", "replace")
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"    warn: {q!r} start={start}: {e}", file=sys.stderr)
                break
            links = re.findall(r"<link>([^<]+)</link>", xml)
            hits = [normalise(l) for l in links if LIBRARY.lower() in l.lower()]
            found.update(hits)
            if len(re.findall(r"<item>", xml)) < PAGE:
                break
            time.sleep(SLEEP)
        print(f"  {q!r}: +{len(found) - before} (total {len(found)})")
    return found


def socrata_rows() -> list[dict]:
    """238 rows, 2016-2018 only, with exact filenames and clean agency names."""
    out: list[dict] = []
    for offset in range(0, 2000, 1000):
        raw = get(f"{SOCRATA}?$limit=1000&$offset={offset}")
        page = json.loads(raw)
        out.extend(page)
        if len(page) < 1000:
            break
    return out


def verify(url: str) -> tuple[int, str, int]:
    """Confirm a candidate is a real, public PDF. Anything else is not ingestible.

    HEAD, NOT GET, and the difference is not a micro-optimisation. Measured against
    this host: a full GET of APPR_DOR_2024-09-30.pdf takes 17.3s (it serves at ~120 KB/s),
    a HEAD takes 0.18s — and the HEAD still carries `content-type: application/pdf` and
    `content-length`, which is everything this function reports. Across ~900 candidates
    that is the difference between a four-hour sweep and a ten-minute one, re-run every
    year under `recheck: annual`.

    What HEAD gives up is proof that the BODY is a PDF rather than a login page wearing
    the right content-type. That check is not lost, only moved: ingestion downloads each
    file and parses it, and a non-PDF fails loudly there. Verifying it twice at 17s a
    file buys nothing.

    Falls back to a 1 KB ranged GET when HEAD is refused (some SharePoint front ends
    answer 405) and checks the %PDF magic directly.
    """
    head = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    try:
        with urllib.request.urlopen(head, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            length = int(r.headers.get("Content-Length") or 0)
            if r.status == 200 and "pdf" in ctype.lower():
                return r.status, ctype, length
            if r.status == 200:
                return _range_probe(url)
            return r.status, ctype, length
    except urllib.error.HTTPError as e:
        if e.code in (405, 501):          # HEAD not allowed here — fall back
            return _range_probe(url)
        return e.code, "", 0
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e), 0


def _range_probe(url: str) -> tuple[int, str, int]:
    """Read the first KB only and trust the magic bytes over any declared type."""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            head_bytes = r.read(1024)
            ctype = r.headers.get("Content-Type", "")
            length = int(r.headers.get("Content-Length") or 0)
            if head_bytes.startswith(b"%PDF"):
                return 200, "application/pdf", length
            return r.status, ctype or "not-a-pdf", length
    except urllib.error.HTTPError as e:
        return e.code, "", 0
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e), 0


# Filename → (agency_code, reporting_year, status). Ordered most specific first. Every
# pattern here was observed in the wild; see the module docstring for examples.
PATTERNS = [
    re.compile(r"^(?P<status>APPRProposed|APPR)_(?P<code>[A-Za-z0-9&.-]+)_"
               r"(?P<year>(19|20)\d{2})[-_]\d{2}[-_]\d{2}\.pdf$", re.I),
    re.compile(r"^(?P<status>APPRProposed|APPR)_(?P<code>[A-Za-z0-9&.-]+)_"
               r"(?P<year>(19|20)\d{2})\.pdf$", re.I),
    re.compile(r"^(?P<code>[A-Za-z0-9&.-]+)\s+(?P<year>(19|20)\d{2})\s+Annual\s+Performance"
               r"\s+Progress\s+Report.*\.pdf$", re.I),
    re.compile(r"^Annual\s+Performance\s+Progress\s+Report\s*-\s*(?P<year>(19|20)\d{2})"
               r"\s*\((?P<code>[^)]+)\)\.pdf$", re.I),
]


# Last-resort year scan. Measured over the 102 names the patterns above could not parse:
# 88 carry exactly one plausible year token, 1 carries two, 13 carry none. Accept only the
# unambiguous 88 — `Updated 2016 APPR_CFB_2017-16-02.pdf` names two years and guessing
# between them would silently file a report under the wrong one.
YEAR_TOKEN = re.compile(r"(20(?:1[3-9]|2[0-6]))")


def parse_name(filename: str) -> dict:
    """Derive what the FILENAME can support. The filename is a hint, not the authority.

    Every APPR states `Reporting Year <YYYY>` on page 1, and that is the field of record —
    ingestion reads it and overrides whatever is guessed here, flagging any disagreement.
    Names cannot be trusted alone: `OBD Annual Performance Progress Report.pdf` carries no
    year at all (it is Reporting Year 2019, and it is the Board of DENTISTRY, not Business
    Development, whose code is the near-identical OBDD).
    """
    name = urllib.parse.unquote(filename)
    for pat in PATTERNS:
        m = pat.match(name)
        if m:
            g = m.groupdict()
            status = g.get("status", "APPR")
            return {
                "agency_code": g.get("code", "").upper() or None,
                "reporting_year": g.get("year"),
                "year_source": "filename",
                # APPRProposed_ is a report of PROPOSED measures. Recorded as a status, not
                # a separate doc_type: three agencies appear in 2024 only as Proposed, so
                # dropping them would read as "did not report" when they did.
                "measure_status": "proposed" if status.lower() == "apprproposed" else "approved",
            }

    years = set(YEAR_TOKEN.findall(name))
    status = "proposed" if "apprproposed" in name.lower().replace(" ", "") else "approved"
    if len(years) == 1:
        return {"agency_code": None, "reporting_year": years.pop(),
                "year_source": "filename-scan", "measure_status": status}
    # Ambiguous or absent: leave it unset so ingestion must resolve it from the document.
    return {"agency_code": None, "reporting_year": None,
            "year_source": None, "measure_status": status}


def load_recorded_shas(manifest_path: Path) -> dict[str, str]:
    """id -> the drift baseline already recorded for that source, for every id that has one.

    `corpus-detect-changes --record-baseline` is the ONLY thing that is meant to WRITE
    `sha256` (oregon-kpm#43) -- it is `content_hash()` of freshly fetched bytes, computed
    by the toolkit, not derivable from anything in this file. This function only READS
    what that tool already wrote, so a re-enumeration can carry it forward instead of
    wiping it back to `""` -- which is exactly how this corpus's drift detection went
    inert the first time: every source compared unequal to everything, forever.

    Missing manifest (first-ever enumeration) or unparseable YAML returns `{}` --
    nothing recorded yet, not an error; a first run must still produce a manifest.
    """
    if not manifest_path.is_file():
        return {}
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return {s["id"]: s["sha256"] for s in (data.get("sources") or [])
            if s.get("id") and s.get("sha256")}


def build(skip_sweep: bool = False, manifest_path: Path = MANIFEST) -> dict:
    # The RSS sweep is ~30 minutes of polite crawling and its result changes once a year.
    # Cache it so re-running for a PARSING change does not re-crawl a state web server,
    # and so `--skip-sweep` can iterate in seconds. Gitignored: it is a network artifact,
    # not a source of truth — the manifest is.
    if skip_sweep and SWEEP_CACHE.exists():
        urls = {l.strip() for l in SWEEP_CACHE.read_text().splitlines() if l.strip()}
        print(f"==> reusing cached sweep: {len(urls)} URLs ({SWEEP_CACHE})")
    else:
        print("==> sweeping the SharePoint search RSS feed")
        urls = sweep_rss()
        SWEEP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SWEEP_CACHE.write_text("\n".join(sorted(urls)) + "\n")
        print(f"==> {len(urls)} distinct /lfo/APPR/ URLs from search")

    print("==> pulling Socrata kvbx-erfw (exact names, 2016-2018)")
    soc = socrata_rows()
    by_file: dict[str, dict] = {}
    for row in soc:
        doc = row.get("report_document") or {}
        fn = doc.get("filename")
        if fn:
            by_file[fn] = {
                "agency": row.get("agency_board_commission_branch"),
                "year": row.get("year"),
                "date_published": row.get("date_published"),
            }
            # Pass the RAW filename. normalise() percent-encodes exactly once; quoting
            # here as well produced `%2520` for every Socrata name containing a space and
            # 404'd 29 candidates, 16 of which are live documents. Encode in one place.
            urls.add(normalise(f"https://www.oregonlegislature.gov{LIBRARY}{fn}"))
    print(f"==> {len(by_file)} exact filenames from Socrata; {len(urls)} candidates total")

    # BEFORE any source is (re)built, so the loop below has something to carry forward
    # instead of a hardcoded `""`. See load_recorded_shas() for why this file is never
    # the one writing a baseline, only ever preserving one.
    recorded = load_recorded_shas(manifest_path)
    print(f"==> {len(recorded)} recorded drift baseline(s) in {manifest_path.name} "
          f"will be carried forward")

    print("==> verifying every candidate resolves to a public PDF")
    sources, unreachable = [], []
    for i, url in enumerate(sorted(urls), 1):
        code, ctype, size = verify(url)
        fn = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        if code != 200 or "pdf" not in ctype.lower():
            unreachable.append({"filename": fn, "url": url, "http": code,
                                "content_type": ctype or "-"})
        else:
            meta = parse_name(fn)
            soc_meta = by_file.get(fn, {})
            sid = fn[:-4] if fn.lower().endswith(".pdf") else fn
            sources.append({
                "id": sid,
                "url": url,
                "format": "pdf",
                "filename": fn,
                "doc_type": "performance_report",
                "agency_code": meta["agency_code"],
                "agency": soc_meta.get("agency"),
                "reporting_year": meta["reporting_year"] or soc_meta.get("year"),
                "year_source": meta["year_source"] or ("socrata" if soc_meta.get("year") else None),
                "measure_status": meta["measure_status"],
                "date_published": soc_meta.get("date_published"),
                "bytes": size,
                "recheck": "annual",
                # Carried forward from the CURRENT manifest, not regenerated here -- this
                # file has no way to compute the detector's hash and must not guess at
                # one. A source rediscovered under the same id keeps whatever baseline
                # was recorded; a genuinely new id starts empty, same as it always did.
                "sha256": recorded.get(sid, ""),
                "why_relevant": "Agency-reported targets, actuals and assessments against "
                                "legislatively approved Key Performance Measures.",
            })
        if i % 25 == 0:
            print(f"    {i}/{len(urls)} verified")
        time.sleep(SLEEP)

    sources.sort(key=lambda s: (str(s["reporting_year"]), str(s["agency_code"]), s["id"]))

    by_year = collections.Counter(str(s["reporting_year"]) for s in sources)
    unparsed = [s["filename"] for s in sources if not s["agency_code"] or not s["reporting_year"]]
    gaps = [f"{y}: no report found by either source" for y in YEARS if not by_year.get(str(y))]

    return {
        "note": (
            "Every upstream source this corpus consumes. Human-approved via PR BEFORE any\n"
            "ingestion. GENERATED by src/enumerate_kpm.py — do not hand-edit; re-run it.\n"
            "\n"
            "Source: the /lfo/APPR document library behind oregonlegislature.gov's KPM page.\n"
            "The library returns 401 to every anonymous LISTING api; individual PDFs under it\n"
            "are public. Names therefore come from SharePoint's search RSS feed plus the\n"
            "Socrata index kvbx-erfw (2016-2018 only). See src/enumerate_kpm.py.\n"
            "\n"
            "Traps recorded so a future re-enumeration does not fall into them:\n"
            "1. NEVER construct a filename. At least six naming conventions are in live use,\n"
            "   including agency-first prose names with spaces. The date in a name is a\n"
            "   publication date and is not derivable.\n"
            "2. RSS links are http:// with unencoded spaces. Normalise both or every fetch\n"
            "   404s in a way that looks exactly like link rot.\n"
            "3. One search query saturates (rows at start=601, none at start=1001), so the\n"
            "   sweep partitions by year and unions the results.\n"
            "4. `measure_status: proposed` marks APPRProposed_ files. They are real reports,\n"
            "   not drafts to discard, and for some agency-years they are the only file.\n"
            "5. Coverage here is a statement about OUR DISCOVERY, not about Oregon. The KPM\n"
            "   page states its own rule: an agency missing for a year is 'non-reported'.\n"
            "6. `sha256` is the drift-detection baseline, not a copy of anything ingestion\n"
            "   computed. It is `corpus_toolkit.repo.content_hash()` of the bytes\n"
            "   `corpus-detect-changes` last fetched for that source (whitespace-normalized\n"
            "   `pdftotext -layout` text for a PDF with a text layer; the raw bytes when\n"
            "   extraction yields under 200 chars, e.g. an image-only scan). It is a\n"
            "   DIFFERENT function and a DIFFERENT input than frontmatter `source_sha256`\n"
            "   (`hash_snapshot()`, over the committed `.txt`) — the two agree only for\n"
            "   scans, where both fall back to raw bytes (oregon-kpm#43). Only\n"
            "   `corpus-detect-changes --record-baseline` writes this field; this\n"
            "   enumerator only ever carries a recorded value forward by `id` and never\n"
            "   clears it — a source new to the manifest starts empty, same as before.\n"
        ),
        "index": INDEX_PAGE,
        "library": "https://www.oregonlegislature.gov" + LIBRARY,
        "recheck": "annual",
        "coverage": {
            "years_attempted": [str(y) for y in YEARS],
            "by_year": dict(sorted(by_year.items())),
            "in_scope": "Annual Performance Progress Reports (APPR and APPRProposed)",
            "out_of_scope": "KPM guidance and system documentation under "
                            "/lfo/KPM Document Library",
        },
        "gaps": gaps or ["none — every attempted year returned at least one report"],
        "unparsed_filenames": unparsed or [],
        "unreachable": unreachable or [],
        "sources": sources,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the manifest would change")
    ap.add_argument("--skip-sweep", action="store_true",
                    help="reuse the cached RSS sweep instead of re-crawling (iteration aid)")
    args = ap.parse_args()

    data = build(skip_sweep=args.skip_sweep)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)

    if args.check:
        if not MANIFEST.exists():
            print("source-manifest.yml is missing", file=sys.stderr)
            return 1
        if MANIFEST.read_text() != text:
            print("source-manifest.yml is out of date — re-run enumerate_kpm.py",
                  file=sys.stderr)
            return 1
        print("source-manifest.yml is current")
        return 0

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(text)
    print(f"\n==> wrote {MANIFEST.relative_to(ROOT)}: {len(data['sources'])} sources")
    print(f"    by year: {data['coverage']['by_year']}")
    if data["unreachable"]:
        print(f"    unreachable: {len(data['unreachable'])}")
    if data["unparsed_filenames"]:
        print(f"    unparsed filenames: {len(data['unparsed_filenames'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
