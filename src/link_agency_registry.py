#!/usr/bin/env python3
"""Crosswalk this corpus's agencies to the ERF agency registry, and keep it honest.

  python3 src/link_agency_registry.py --check              # CI: committed data only
  python3 src/link_agency_registry.py --verify-registry    # local: slugs + exact-basis vs ERF
  python3 src/link_agency_registry.py --unresolved-report  # human work list

WHY A CROSSWALK AND NOT A CITATION SCHEME. `siblings:` resolution is exact lookup of a
DOCUMENT ID pulled out of a citation string by regex, against a published corpus-index.json
whose rows are [title, doc_type, path]. An agency is not a document there -- ERF's
187-organization registry lives in `_meta/catalog/agencies.yml` and is never published to
that index -- and agency identity is a frontmatter field, not a string an APPR cites. No
`register_scheme` can express this join. src/citation_schemes.py says the same thing about
the budget join, for the same reason.

WHY THE TABLE LIVES HERE AND NOT IN ERF. The precedent is
`oregon-policy-repo/src/link_budget_codes.py`, which writes `budget_agency_code` INTO ERF's
registry entries. That works for one consumer and scales badly: ERF would carry one external
id per corpus that cites it, and `agency_key` is this corpus's own derived concept. ERF's own
`_meta/agency-profiles.yml` is the counter-precedent -- a side-file keyed on registry slugs,
owned by the thing that needs it. Correctness still belongs to ERF: --verify-registry
resolves every slug against the real registry, and a slug this file invents is a failure.

WHAT IS DELIBERATELY DIFFERENT FROM link_budget_codes.py's audit(). That one fails when two
budget codes claim one slug, because two codes claiming one agency would silently merge
spending. Here MANY-TO-ONE IS CORRECT AND EXPECTED: an agency that renamed itself appears
under both names across thirteen reporting years, and `Board of Psychology` and
`Psychologist Examiners, Board of` must both reach
`mental-health-regulatory-agency-oregon-board-of-psychology`. Collapsing them is the point.
So the check is that every claimant carries a `note` saying why, not that claimants are
unique.

CI MUST NOT NEED ERF. `--check` validates only what is committed here: that every agency_key
in reports/ is accounted for, that nothing is both mapped and unmapped, and that every
unmapped entry states a reason. Referential integrity against the sibling is
--verify-registry, run locally and in review, exactly as oregon-budget's build_joins.py
splits the two.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CROSSWALK = ROOT / "_meta" / "agency-crosswalk.yml"
REPORT_OUT = ROOT / "_meta" / "unresolved-agencies.md"

REGISTRY_CORPUS = "executive-regulatory-frameworks"
# Probed in order, --registry overrides. Same shape as oregon-budget's
# ERF_REGISTRY_CANDIDATES: ERF is checked out under its own name on some machines and under
# its former repo name `oregon-policy-repo` on others.
REGISTRY_CANDIDATES = [
    ROOT.parent / "executive-regulatory-frameworks" / "_meta" / "catalog" / "agencies.yml",
    ROOT.parent / "oregon-policy-repo" / "_meta" / "catalog" / "agencies.yml",
]

BASES = {"exact", "alias", "successor", "manual"}


def frontmatter(path: Path) -> dict:
    parts = path.read_text(encoding="utf-8", errors="replace").split("---", 2)
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}


def corpus_keys() -> dict[str, set[str]]:
    """{agency_key: {stated names}} over every committed report."""
    out: dict[str, set[str]] = {}
    for p in sorted(REPORTS.glob("*.md")):
        fm = frontmatter(p)
        k = fm.get("agency_key")
        if k:
            out.setdefault(k, set()).add(fm.get("agency") or "")
    return out


def load_crosswalk() -> dict:
    if not CROSSWALK.is_file():
        sys.exit(f"missing {CROSSWALK.relative_to(ROOT)}")
    return yaml.safe_load(CROSSWALK.read_text(encoding="utf-8")) or {}


def find_registry(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    return next((p for p in REGISTRY_CANDIDATES if p.is_file()), None)


def registry_slugs(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {o["slug"] for o in data.get("organizations") or [] if o.get("slug")}


def registry_oar_names(path: Path) -> dict[str, str]:
    """slug -> oar_name, the registry string this crosswalk's `exact` claims are about.

    ERF's ADR 0003 splits the registry's one name field in two: `name` becomes the body's
    STATUTORY name, while `oar_name` keeps the OAR chapter title and "remains the string
    OAR-derived joins must match". This crosswalk was built against the OAR chapter titles,
    so `oar_name` is the side of that split its `basis: exact` entries were checked against.
    Naming the field is the point: after ERF#168 "matches the registry name" is two claims,
    and one that does not say which it means has stopped asserting anything.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {o["slug"]: o["oar_name"] for o in data.get("organizations") or []
            if o.get("slug") and o.get("oar_name")}


# --- BEGIN VERBATIM SHARED BLOCK (norm_variants / names_agree) -------------------------
# Kept BYTE-IDENTICAL with the copy in oregon-audits/src/link_agency_registry.py, following
# the convention src/federal_ids.py states: "copy it verbatim ... both sides then compute
# the same [answers] by construction instead of by agreement". Both corpora define
# `basis: exact` with the same four permitted moves, so they must normalise identically or
# the same pair of names is exact in one repo and not the other. NOT yet covered by a
# parity gate like the federal_ids.py one in .github/workflows/ci.yml -- if this block
# grows a third copy, wire that gate before it drifts.
def norm_variants(name: str) -> set[str]:
    """Every reading the crosswalk note permits `basis: exact` to use.

    The note lists the allowed moves as "case, punctuation, comma-inversion, a leading
    Oregon" -- a SET of moves, not a pipeline that must apply all of them. A comma does two
    different jobs in these strings: catalog inversion ("Administrative Services, Department
    of") and a parent/child qualifier ("Secretary of State, Audits Division"). Inverting the
    second is wrong and dropping the comma in the first is not enough, so both readings are
    produced and a match on either is a match.

    Written this way because forcing a single reading is a MEASURED bug, not a hypothetical:
    always-invert reported 'Secretary of State Audits Division' as failing to match an
    oar_name that is the same name with a comma in it.
    """
    n = name.strip().replace("\u2019", "'")
    readings = {n.replace(",", " ")}
    if "," in n:
        head, tail = n.rsplit(",", 1)
        readings.add(f"{tail.strip()} {head.strip()}")
    out = set()
    for r in readings:
        r = " ".join(r.lower().replace(".", "").split())
        for pre in ("oregon ", "state of oregon "):
            if r.startswith(pre):
                r = r[len(pre):]
        out.add(r)
    return out


def names_agree(a: str, b: str) -> bool:
    """True when two names are the same name under any reading the note permits."""
    return bool(norm_variants(a) & norm_variants(b))
# --- END VERBATIM SHARED BLOCK ---------------------------------------------------------


def verify_exact_basis(cw: dict, keys: dict[str, set[str]],
                       oar_names: dict[str, str]) -> list[str]:
    """Entries claiming `basis: exact` where NO stated agency name matches the registry's
    `oar_name` for the mapped slug.

    `agency_key` is this corpus's own derived token ("advocate-public-records"), not a name,
    so the thing an `exact` claim can be about is the agency names the reports actually
    state -- which corpus_keys() already collects. Many keys map to one slug on purpose, and
    a renamed agency states different names across reporting years, so ANY stated name
    matching is enough.

    REPORTED, NOT FAILED. A wrong `basis` does not break the join -- the join is the slug,
    which verify_registry() checks and which ERF#168 does not touch -- it misdescribes why a
    human accepted the mapping. Several of these are renames the vocabulary already has
    better words for (`successor`, `alias`), and re-basing one is a curation decision with a
    reviewer's name on it, not something a script should do while nobody is looking.
    """
    out = []
    for k, v in sorted((cw.get("mapping") or {}).items()):
        if not isinstance(v, dict) or v.get("basis") != "exact":
            continue
        want = oar_names.get(v.get("slug") or "")
        stated = {n for n in keys.get(k, set()) if n}
        if want is None or not any(names_agree(n, want) for n in stated):
            out.append(f"{k!r} claims basis: exact but no stated name {sorted(stated)!r} "
                       f"matches the registry's oar_name {want!r} for {v.get('slug')!r}")
    return out


def stamp_state(mapping: dict) -> tuple[int, int]:
    """(documents whose agency is mapped, of those, how many carry the stamp)."""
    want = stamped = 0
    for p in sorted(REPORTS.glob("*.md")):
        fm = frontmatter(p)
        if (fm.get("agency_key") or "") in mapping:
            want += 1
            stamped += bool(fm.get("agency_registry_slug"))
    return want, stamped


def check(cw: dict, keys: dict[str, set[str]]) -> list[str]:
    """Committed-data-only validation. Never touches the sibling."""
    mapping = cw.get("mapping") or {}
    unmapped = cw.get("unmapped") or {}
    bad = []

    # A HALF-STAMPED CORPUS IS THE FAILURE THIS CATCHES. Editing the crosswalk without
    # re-running the ingester leaves documents whose agency IS mapped carrying no link, and
    # nothing else notices: check_guardrails only verifies that a stamp which EXISTS agrees,
    # so an absent one passes every other gate. The corpus then answers "no registry link"
    # for an agency the crosswalk maps, which reads as a decision rather than as staleness.
    want, stamped = stamp_state(mapping)
    if want != stamped:
        bad.append(f"{want - stamped} document(s) have a mapped agency_key but no "
                   f"agency_registry_slug -- re-run `python3 src/ingest_kpm.py`")

    both = set(mapping) & set(unmapped)
    if both:
        bad.append(f"{len(both)} key(s) both mapped and unmapped: {sorted(both)[:5]}")

    missing = sorted(set(keys) - set(mapping) - set(unmapped))
    if missing:
        # An agency nobody has classified is the state this file exists to make impossible.
        bad.append(f"{len(missing)} agency_key(s) in reports/ are in neither mapping nor "
                   f"unmapped: {missing[:5]}")

    stale = sorted((set(mapping) | set(unmapped)) - set(keys))
    if stale:
        bad.append(f"{len(stale)} crosswalk key(s) match no document: {stale[:5]}")

    for k, v in mapping.items():
        if not isinstance(v, dict) or not v.get("slug"):
            bad.append(f"{k}: mapping entry has no slug")
            continue
        if v.get("basis") not in BASES:
            bad.append(f"{k}: basis={v.get('basis')!r} (must be one of {sorted(BASES)})")
        # Anything not a plain exact-name match asserts an identity the names do not state,
        # so it has to say why in prose. This is the rule that stops a fuzzy suggestion
        # being quietly promoted into a fact.
        if v.get("basis") in {"alias", "successor", "manual"} and not v.get("note"):
            bad.append(f"{k}: basis={v['basis']} requires a note explaining the identity")

    for k, v in unmapped.items():
        reason = v.get("reason") if isinstance(v, dict) else None
        if not reason:
            # "we looked and there is no counterpart" and "nobody has looked yet" must not
            # be the same state.
            bad.append(f"{k}: unmapped entries require a reason")
    return bad


def verify_registry(cw: dict, slugs: set[str]) -> list[str]:
    return [f"{k}: slug {v['slug']!r} is not in the ERF registry"
            for k, v in (cw.get("mapping") or {}).items()
            if isinstance(v, dict) and v.get("slug") and v["slug"] not in slugs]


def unresolved_report(cw: dict, keys: dict[str, set[str]], slugs: set[str] | None) -> str:
    """A work list bucketed by the response each case needs, not a flat list of failures.

    The buckets need OPPOSITE actions -- one is human confirmation, one is a note, one is
    nothing at all -- and a single list invites treating them the same. Fuzzy suggestions
    appear here and ONLY here: this file is read by a person, and `--check` never consults
    them.
    """
    mapping = cw.get("mapping") or {}
    unmapped = cw.get("unmapped") or {}
    review = {k: v for k, v in mapping.items() if isinstance(v, dict) and v.get("review")}
    # Confirmed assertions stay listed rather than disappearing once signed off. A curated
    # mapping is the corpus asserting an identity its documents do not state, and that claim
    # should remain visible and attributable -- an unreviewed guess and a reviewed decision
    # look identical the moment the record of who accepted it is dropped.
    signed = {k: v for k, v in mapping.items() if isinstance(v, dict) and v.get("reviewed_by")}
    todo = sorted(set(keys) - set(mapping) - set(unmapped))

    out = ["# Unresolved agencies", "",
           "Generated by `src/link_agency_registry.py --unresolved-report`. Do not hand-edit.",
           "", f"- mapped: {len(mapping)}", f"- unmapped (recorded, with reason): {len(unmapped)}",
           f"- awaiting human confirmation: {len(review)}",
           f"- confirmed by a reviewer: {len(signed)}",
           f"- unclassified: {len(todo)}", ""]

    if signed:
        out += ["## Confirmed curated mappings", "",
                "Identities the names do not state outright, asserted by this corpus and "
                "accepted by a reviewer. Listed so the claim stays visible and attributable.",
                "", "| agency_key | slug | basis | confirmed by | on |", "|---|---|---|---|---|"]
        for k, v in sorted(signed.items()):
            out.append(f"| `{k}` | `{v['slug']}` | {v.get('basis')} | {v['reviewed_by']} | "
                       f"{v['reviewed_on']} |")
        out.append("")

    if review:
        out += ["## Awaiting confirmation", "",
                "Mapped on an asserted identity rather than a name match. Confirm against the "
                "ERF registry, then drop `review: required`.", "",
                "| agency_key | stated as | slug | basis | why |", "|---|---|---|---|---|"]
        for k, v in sorted(review.items()):
            nm = sorted(keys.get(k, {""}))[0]
            out.append(f"| `{k}` | {nm} | `{v['slug']}` | {v.get('basis')} | "
                       f"{(v.get('note') or '').replace('|', '\\|')} |")
        out.append("")

    if todo:
        out += ["## Unclassified", "",
                "Neither mapped nor recorded as absent. Suggestions are FOR A HUMAN and are "
                "never applied by any check -- fuzzy matching proposed "
                "`Psychologist Examiners, Board of` -> `board-of-geologist-examiners`.", "",
                "| agency_key | stated as | suggestion |", "|---|---|---|"]
        for k in todo:
            nm = sorted(keys.get(k, {""}))[0]
            s = (difflib.get_close_matches(k, sorted(slugs), n=1, cutoff=0.6)[:1]
                 if slugs else [])
            out.append(f"| `{k}` | {nm} | {'`' + s[0] + '`' if s else '_none_'} |")
        out.append("")

    if unmapped:
        out += ["## Recorded as having no ERF counterpart", "",
                "These are a decision, not a gap. ERF's registry is keyed on OAR chapter "
                "assignment, so a body issuing no administrative rules is absent by "
                "construction.", "", "| agency_key | reason |", "|---|---|"]
        for k, v in sorted(unmapped.items()):
            r = (v.get("reason") if isinstance(v, dict) else "") or ""
            out.append(f"| `{k}` | {r.replace('|', '\\|')} |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--verify-registry", action="store_true")
    ap.add_argument("--unresolved-report", action="store_true")
    ap.add_argument("--registry", help="path to ERF's _meta/catalog/agencies.yml")
    args = ap.parse_args()

    cw, keys = load_crosswalk(), corpus_keys()
    mapping, unmapped = cw.get("mapping") or {}, cw.get("unmapped") or {}

    if args.check:
        problems = check(cw, keys)
        for p in problems:
            print(f"FAIL  {p}", file=sys.stderr)
        covered = len(set(keys) & (set(mapping) | set(unmapped)))
        print(f"{len(keys)} agency_key(s); {len(mapping)} mapped, {len(unmapped)} recorded "
              f"as absent, {covered}/{len(keys)} accounted for.")
        return 1 if problems else 0

    if args.verify_registry:
        reg = find_registry(args.registry)
        if reg is None:
            # NOT a pass. A missing sibling means the check did not run, and exiting 0 would
            # report "verified" for something nobody verified.
            print("SKIPPED: no ERF agency registry found. Checked:\n  " +
                  "\n  ".join(str(p) for p in REGISTRY_CANDIDATES) +
                  "\nClone executive-regulatory-frameworks beside this repo or pass "
                  "--registry. This is NOT a pass.", file=sys.stderr)
            return 2
        slugs = registry_slugs(reg)
        problems = verify_registry(cw, slugs)
        for p in problems:
            print(f"FAIL  {p}", file=sys.stderr)
        print(f"{len(mapping)} mapped slug(s) checked against {len(slugs)} organizations "
              f"in {reg}.")

        # The `exact` claims, checked against the registry field they are claims ABOUT.
        # Listed rather than failed -- see verify_exact_basis().
        oar_names = registry_oar_names(reg)
        n_exact = sum(1 for v in mapping.values()
                      if isinstance(v, dict) and v.get("basis") == "exact")
        mislabelled = verify_exact_basis(cw, keys, oar_names)
        for m in mislabelled:
            print(f"REVIEW  {m}", file=sys.stderr)
        print(f"{n_exact - len(mislabelled)}/{n_exact} basis: exact entries match the "
              f"registry's oar_name; {len(mislabelled)} need a reviewer to re-base or "
              f"re-word (not a join failure -- the join is the slug).")
        return 1 if problems else 0

    if args.unresolved_report:
        reg = find_registry(args.registry)
        slugs = registry_slugs(reg) if reg else None
        REPORT_OUT.write_text(unresolved_report(cw, keys, slugs), encoding="utf-8")
        print(f"wrote {REPORT_OUT.relative_to(ROOT)}"
              f"{'' if slugs else ' (no registry found -- suggestions omitted)'}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
