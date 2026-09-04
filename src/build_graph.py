#!/usr/bin/env python3
"""Build _meta/graph.json — the node/edge artifact the toolkit reads.

WHY THIS FILE EXISTS IN THE TEMPLATE. The toolkit only ever READS the graph; it
never writes one. Without it `resolve_citation`, `graph_neighbors` and
`authority_chain` return nothing at all — not an error, just empty results — and
`corpus-validate-frontmatter --changed` loses the universe it resolves
relationship targets against. That failure is completely silent, so a corpus can
look healthy while its citation resolution does nothing. Every corpus needs a
graph builder; this is a working generic one.

It is deliberately simple: nodes from frontmatter, edges from each document's
`relationships` block. That is enough for any corpus whose relationships are
hand-authored or written by its own ingester.

MOST CORPORA WILL OUTGROW IT. The mature reference corpus
(OregonAI/executive-regulatory-frameworks) derives edges MECHANICALLY instead —
parsing authority citations out of each document's own text, resolving statute
renumbering, and keeping implements/implemented_by mirrors symmetric — because
hand-authored edges do not scale to 69,395 documents and drift from the text
they claim to represent. When this corpus reaches that point, replace the
`edges_for` function below rather than bolting derivation on elsewhere.

Cross-corpus targets: an edge whose `to` is a citation string ("ORS 192.311")
rather than a local id is CORRECT and expected — the referenced document lives
in a sibling corpus (see corpus.yml `siblings:`). Those are emitted as-is and
counted separately, so an unresolvable `to` means "look next door", not "broken".

  python3 src/build_graph.py           # write _meta/graph.json
  python3 src/build_graph.py --check   # exit 1 if stale (wire this into CI)

Frontmatter parsing and the content walk come from the toolkit (`corpus_toolkit.repo`), so
this builder sees exactly the documents the validator sees -- one scan rule, not two kept in
step by hand.
"""
import json
import sys
from pathlib import Path

from corpus_toolkit import config as _config
from corpus_toolkit.repo import content_files, parse_frontmatter

ROOT = Path(__file__).resolve().parent.parent
CONFIG = _config.load(ROOT / "_meta/corpus.yml")

REL_KEYS = ("implements", "implemented_by", "references_external", "related", "supersedes")


def edges_for(fm: dict) -> list[dict]:
    """Edges out of one document. Replace this when mechanical derivation is
    needed (see the module docstring)."""
    out = []
    for key in REL_KEYS:
        for target in (fm.get("relationships") or {}).get(key) or []:
            out.append({"from": fm["id"], "type": key, "to": target})
    return out


def series_edges(metas: list[dict]) -> list[dict]:
    """MECHANICAL derivation: link each agency's report to the year before it.

    The template invites replacing `edges_for` when a corpus needs derived rather than
    hand-authored edges, and this corpus needs it for a reason specific to the source.

    Every APPR carries a ROLLING FIVE-YEAR HISTORY, so the same (agency, measure, year)
    appears in up to five documents and 489 of those pairs DISAGREE -- restatements and
    methodology changes recorded in _meta/series.json. A reader who lands on one report has
    no way to reach the others from the document alone: the reports never cite each other,
    because each is written as a standalone submission to the Legislature.

    So the chain is derived from (agency, reporting_year), not extracted from text. Only
    CONSECUTIVE reporting years are linked. A gap is left as a gap -- LFO's own rule is that
    a missing agency-year means the agency was "non-reported", and bridging that with an
    edge would erase the fact.

    Hand-authoring these was never an option: 789 sources across 83 agencies and 13 years,
    and every one of them would have to be revisited whenever a year is added.
    """
    by_agency: dict[str, dict[int, str]] = {}
    for m in metas:
        # KEYED ON agency_key, NOT the stated name. The cover page reorders itself between
        # years -- `Board of Accountancy` becomes `Accountancy, Board of` -- and matching on
        # the string broke the chain at exactly those points, silently, because a missing
        # edge is indistinguishable from an agency that did not report that year. 152 stated
        # names across the corpus are 98 agencies.
        agency = m.get("agency_key") or m.get("agency")
        year = m.get("reporting_year")
        if not agency or not year:
            continue
        try:
            by_agency.setdefault(agency, {})[int(year)] = m["id"]
        except (TypeError, ValueError):
            continue
    out = []
    for years in by_agency.values():
        for y, doc_id in sorted(years.items()):
            prev = years.get(y - 1)
            if prev:
                out.append({"from": doc_id, "type": "related", "to": prev})
    return out


def build() -> dict:
    nodes, edges, metas = [], [], []
    for path in content_files(CONFIG):
        fm, _body = parse_frontmatter(path)
        if not fm.get("id"):
            continue
        nodes.append({"id": fm["id"], "title": fm.get("title", ""),
                      "doc_type": fm.get("doc_type", ""),
                      "status": fm.get("status", ""),
                      "path": str(path.relative_to(ROOT))})
        metas.append(fm)
        edges.extend(edges_for(fm))
    edges.extend(series_edges(metas))
    local = {n["id"] for n in nodes}
    return {"corpus": CONFIG.id,
            "n_nodes": len(nodes), "n_edges": len(edges),
            "n_edges_external": sum(1 for e in edges if e["to"] not in local),
            "nodes": nodes, "edges": edges}


def main():
    graph = build()
    text = json.dumps(graph, ensure_ascii=False, indent=1) + "\n"
    out = CONFIG.graph_path
    if "--check" in sys.argv:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print(f"{out.relative_to(ROOT)} is stale — run: python3 src/build_graph.py")
            sys.exit(1)
        print(f"{out.relative_to(ROOT)} is current.")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    ext = f", {graph['n_edges_external']} pointing outside this corpus" if graph["n_edges_external"] else ""
    print(f"wrote {out.relative_to(ROOT)}: {graph['n_nodes']} nodes, {graph['n_edges']} edges{ext}")


if __name__ == "__main__":
    main()
