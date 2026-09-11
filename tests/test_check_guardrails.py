"""oregon-kpm#44: a document's stamped agency_registry_basis must agree with the crosswalk.

`src/link_agency_registry.py` records, per agency, HOW the join to the ERF registry was
made -- `basis: exact` is a mechanical name match, `alias`/`successor` are a human
assertion. Before this fix, `check_registry_link_agrees()` verified only that a
document's `agency_registry_slug` matches the crosswalk's `slug` for its `agency_key`; it
never looked at `basis` at all, so a document could carry `agency_registry_basis: exact`
long after a reviewer re-based its crosswalk entry to `successor` (as #52 actually did for
nine entries) and nothing would catch it. That is exactly the "half-stamped corpus" this
function otherwise guards against -- a drift that is silent by construction because the
slug alone still agrees.

This test exercises `check_registry_link_agrees()` at the same seam it already uses for
the slug: a list of `(path, frontmatter)` tuples in, a list of problem strings out. The
crosswalk it reads from disk is redirected via `ROOT`, exactly the way `tmp_path` fixtures
elsewhere in this repo substitute a controlled file for the real corpus.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import check_guardrails as cg  # noqa: E402

_CROSSWALK_YAML = """
mapping:
  youth-authority:
    slug: oregon-youth-authority
    basis: successor
    note: renamed across reporting years
    reviewed_by: '@morficflux'
    reviewed_on: '2026-08-01'
  accountancy:
    slug: oregon-board-of-accountancy
    basis: exact
"""


def _crosswalk(tmp_path, monkeypatch):
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "agency-crosswalk.yml").write_text(_CROSSWALK_YAML, encoding="utf-8")
    monkeypatch.setattr(cg, "ROOT", tmp_path)


class TestCheckRegistryLinkAgrees:
    def test_stamped_basis_disagreeing_with_the_crosswalk_is_flagged(self, tmp_path, monkeypatch):
        _crosswalk(tmp_path, monkeypatch)
        docs = [(Path("appr-oya-2020.md"), {
            "agency_key": "youth-authority",
            "agency_registry_slug": "oregon-youth-authority",
            "agency_registry_corpus": "executive-regulatory-frameworks",
            # WRONG: the crosswalk records basis: successor for this key.
            "agency_registry_basis": "exact",
        })]
        problems = cg.check_registry_link_agrees(docs)
        assert any("agency_registry_basis" in p and "successor" in p for p in problems)

    def test_stamped_basis_agreeing_with_the_crosswalk_is_silent(self, tmp_path, monkeypatch):
        _crosswalk(tmp_path, monkeypatch)
        docs = [(Path("appr-boa-2020.md"), {
            "agency_key": "accountancy",
            "agency_registry_slug": "oregon-board-of-accountancy",
            "agency_registry_corpus": "executive-regulatory-frameworks",
            "agency_registry_basis": "exact",
        })]
        assert cg.check_registry_link_agrees(docs) == []

    def test_missing_review_metadata_for_a_reviewed_entry_is_flagged(self, tmp_path, monkeypatch):
        _crosswalk(tmp_path, monkeypatch)
        docs = [(Path("appr-oya-2020.md"), {
            "agency_key": "youth-authority",
            "agency_registry_slug": "oregon-youth-authority",
            "agency_registry_corpus": "executive-regulatory-frameworks",
            "agency_registry_basis": "successor",
            # reviewed_by/reviewed_on omitted, though the crosswalk entry carries both.
        })]
        problems = cg.check_registry_link_agrees(docs)
        assert any("agency_registry_reviewed_by" in p for p in problems)
        assert any("agency_registry_reviewed_on" in p for p in problems)
