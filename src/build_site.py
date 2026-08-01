#!/usr/bin/env python3
"""Build the GitHub Pages site into ./site/ (gitignored; produced at deploy time).

    python3 src/build_site.py

Chrome, CSS and the cross-corpus contracts live in `corpus_toolkit.site`. This file owns
only what is specific to this corpus: its numbers and what they mean.

THIS REPLACES the reusable publish-index workflow — the two must never both exist here,
because they fight over the `pages` concurrency group.
"""
import collections
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from corpus_toolkit import config as config_mod                       # noqa: E402
from corpus_toolkit.site import Page, Section, Tile, build            # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent


def stats() -> dict:
    years, agencies = collections.Counter(), set()
    for p in (REPO / "reports").rglob("*.md"):
        fm = yaml.safe_load(p.read_text().split("---", 2)[1]) or {}
        if fm.get("reporting_year"):
            years[str(fm["reporting_year"])] += 1
        agencies.add(fm.get("agency_key") or fm.get("agency"))
    g = json.loads((REPO / "_meta/graph.json").read_text())
    return {"reports": g["n_nodes"], "edges": g["n_edges"],
            "agencies": len(agencies), "first": min(years), "last": max(years)}


def main() -> int:
    s = stats()
    out = build(Page(
        config=config_mod.load(REPO / "_meta/corpus.yml"),
        repo="oregon-kpm",
        title="Oregon Key Performance Measures — agency targets and actuals",
        description=("A non-authoritative, machine-readable mirror of Oregon agency Annual "
                     "Performance Progress Reports — the targets and actuals every agency "
                     "reports to the Legislature."),
        eyebrow="Oregon · Legislative Fiscal Office",
        headline="What agencies told the Legislature they achieved",
        lede_html=(
            f"<b>{s['reports']} Annual Performance Progress Reports</b> from "
            f"<b>{s['agencies']} agencies</b>, {s['first']} to {s['last']}, with the "
            "year-on-year series derived across them. Every number here is the agency's own "
            "account of itself."),
        disclaimer=("NON-AUTHORITATIVE reference — not the official performance report. "
                    "Always verify against the Legislative Fiscal Office."),
        tiles=[
            Tile("Progress reports", f"{s['reports']:,}", f"{s['first']} to {s['last']}"),
            Tile("Agencies", f"{s['agencies']}",
                 "each reporting its own key performance measures"),
            Tile("Series edges", f"{s['edges']:,}",
                 "one report linked to the same agency's report the year before"),
        ],
        sections=[
            Section("A reported number is a claim, not a measurement", """
    <ul class="plain">
      <li>A Key Performance Measure is <b>the agency's own account of its own
        performance</b>, submitted to the Legislature. This corpus mirrors that claim with
        attribution; it does not verify it, and nothing here should be read as
        confirmation that a target was met.</li>
      <li>Targets and actuals are recorded as the agency stated them, including where a
        measure changed definition between years — which is exactly when a year-on-year
        comparison stops meaning what it appears to mean.</li>
      <li>The complement is <a href="https://oregonai.github.io/oregon-audits/">Audits</a>,
        where an independent body examined some of the same programs.</li>
    </ul>"""),
            Section("The outcome side of the money", """
    <ul class="plain">
      <li><a href="https://oregonai.github.io/oregon-budget/">Budget &amp; Expenditure</a>
        answers what was appropriated and spent. This corpus answers what the agency then
        said it achieved with it. Neither answers whether that is true.</li>
      <li>Agencies are keyed against the shared registry, so the same agency is the same
        entity across corpora rather than a name that happens to match.</li>
    </ul>"""),
            Section("For agents", """
    <ul class="plain">
      <li><b>MCP server</b> — tools: <code>search_corpus</code>, <code>get_document</code>,
        <code>resolve_citation</code>, <code>corpus_overview</code>,
        <code>graph_neighbors</code>, <code>authority_chain</code>.</li>
      <li><b>Every report carries provenance</b> — source URL, retrieval date and a content
        hash.</li>
    </ul>"""),
        ],
        footer_note=("Unofficial and non-authoritative; not affiliated with the Oregon "
                     "Legislative Fiscal Office."),
    ))
    print(f"built site/ — {s['reports']} reports, {s['agencies']} agencies")
    print(f"  corpus-index.json: {out['index']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
