#!/usr/bin/env python3
"""Citation schemes this corpus resolves, registered with the MCP framework.

Loaded via `plugins.citation_module` in _meta/corpus.yml. Importing this module is the
whole contract -- `register_scheme` calls happen at import time.

REGISTER_SCHEME COMPILES WITH NO FLAGS. `re.IGNORECASE` is not applied, so every pattern
here spells out its own case handling. oregon-records-retention hit exactly this and left a
note, and oregon-audits repeated the note; repeating it again rather than the mistake.

INBOUND RESOLUTION CANNOT USE A FORMAT STRING, unlike every sibling corpus.

Elsewhere a citation maps arithmetically onto a document id -- "Report No. 2024-14" ->
`2024-14`, "ORS 192.355" -> `ors-192.355`. Here it cannot, because THE DOCUMENT IDS ARE
SLUGGED UPSTREAM FILENAMES and upstream uses at least six naming conventions:

    appr-boa-2016-10-07                          APPR_BOA_2016-10-07.pdf
    appr-ost-2023                                APPR_OST_2023.pdf
    obd-annual-performance-progress-report       OBD Annual Performance Progress Report.pdf
    odag-kpm-reprot                              ODAg KPM Reprot.pdf   (upstream typo)

Nothing derives `obd-annual-performance-progress-report` from "APPR OBD 2019". So the
resolver consults a LOOKUP built from the corpus's own frontmatter, lazily on first use
rather than at import, so loading this module stays cheap for a server that may never
resolve a citation.

THE JOIN THIS CORPUS EXISTS FOR IS NOT A CITATION SCHEME. `oregon-kpm` -> `oregon-budget`
("dollars appropriated against outcomes reported, per agency per biennium") matches on
AGENCY and BIENNIUM, not on a citation string an APPR contains. No `register_scheme` can
express it, and pretending otherwise with a `corpus=` scheme would produce a pattern that
matches nothing while reading like a working feature. It belongs in the graph, and it is
recorded here so the next reader does not go looking for it.
"""
from __future__ import annotations

import re
from pathlib import Path

from corpus_toolkit.mcp.framework import register_scheme

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

_INDEX: dict[tuple[str, str], list[str]] | None = None


def _index() -> dict[tuple[str, str], list[str]]:
    """(agency_code|agency_slug, reporting_year) -> [doc_id]. Built once, on first use."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx: dict[tuple[str, str], list[str]] = {}
    for md in REPORTS.glob("*.md"):
        head = md.read_text(errors="replace").split("---", 2)
        if len(head) < 3:
            continue
        fm = {}
        for line in head[1].splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("'\"")
        year, doc_id = fm.get("reporting_year"), fm.get("id")
        if not year or not doc_id:
            continue
        keys = set()
        if fm.get("agency_code") and fm["agency_code"] != "null":
            keys.add(fm["agency_code"].upper())
        if fm.get("agency"):
            keys.add(re.sub(r"[^A-Z0-9]+", "-", fm["agency"].upper()).strip("-"))
        for k in keys:
            idx.setdefault((k, str(year)), []).append(doc_id)
    _INDEX = idx
    return idx


def _resolve_appr(m) -> list[str]:
    key = (m["code"].upper().replace(" ", "-"), m["year"])
    return sorted(_index().get(key, []))


# ---------------------------------------------------------------- inbound: our own reports
#
# The citation this corpus stamps on every document: `APPR <CODE> <YEAR>`, where CODE is the
# upstream agency code where one exists and a slug of the agency's own name where it does
# not. Both forms are indexed, because a caller quoting a report will have whichever the
# document itself carried.
register_scheme(
    "appr",
    r"APPR\s+(?P<code>[A-Z][A-Z0-9&.\- ]{1,40}?)\s+(?P<year>(?:19|20)\d{2})",
    resolver=_resolve_appr,
)

# KPM NUMBER IS NOT AN IDENTIFIER, so there is deliberately NO `kpm-number` scheme.
#
# The seed proposed citing a measure as (agency, KPM number, year). Numbers are per-agency
# and REUSED as measures retire, so `KPM #3` means different things in different years for
# the same agency -- a scheme keyed on it would resolve confidently to the wrong measure,
# which is worse than not resolving. The derived series in _meta/series.json keys on
# normalised measure TEXT and carries the number as an attribute; that is where a
# measure-level lookup belongs.

# ---------------------------------------------------------------- outbound: sibling corpora
#
# MEASURED BEFORE DECLARING, the discipline oregon-audits established. Re-measured over the
# full 785-document corpus; the figures below replace the ones taken on the 229-document
# Stage 2 slice, which understated this edge by roughly a factor of four:
#
#     ORS   202 occurrences,  77 documents,  36 distinct sections   (was 49 / 22)
#     OAR    39 occurrences,  19 documents,  11 distinct rules      (was  9 /  5)
#     CFR     0 occurrences
#
# Resolution against ERF's published index: 26 of 36 ORS targets (72%) and 9 of 11 OAR
# (82%); by occurrence, 127 of 202 and 37 of 39. The unresolved remainder is ERF coverage
# -- land use (197.296-197.299, 197A.320), 9-1-1 (403.x), air quality (468A.x) -- not
# citations this corpus got wrong.
#
# It is still a NARROWER edge than the audits one, and saying so matters: oregon-audits
# registered its federal edge on 448 occurrences, and an APPR is numbers and narrative
# rather than legal citation. But at 77 documents it is no longer the marginal case the
# original note described, and it carries more weight than "two cheap patterns" implied.
#
# THE SCHEMES ARE ONLY HALF THE WIRING. Until 2026-08-01 `_meta/corpus.yml` declared no
# `siblings:` block, so every one of these citations matched a scheme and then resolved
# against nothing, returning `sibling_unavailable`. A registered scheme with no matching
# sibling entry is worse than no scheme at all -- it reads as a working edge that finds
# nothing. Change one and check the other.
#
# The section number requires >= 3 digits deliberately: PDF extraction splits long numbers
# across line breaks, and a looser pattern resolves "ORS 238.4" confidently to a section
# that does not exist.
register_scheme("ors-section", r"ORS\s+(?P<num>\d+[A-Z]?\.\d{3,})",
                "ors-{num}", corpus="executive-regulatory-frameworks")
register_scheme("oar-rule", r"OAR\s+(?P<num>\d{3}-\d{3}-\d{4})",
                "oar-{num}", corpus="executive-regulatory-frameworks")
