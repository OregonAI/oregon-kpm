"""oregon-kpm#44: the linker stamps `agency_registry_slug` but drops the crosswalk's
`basis` on the floor, so a consumer reading a document's frontmatter cannot tell a
mechanical name match (`basis: exact`) from a human judgement (`alias`/`successor`) --
several of which carry `reviewed_by`/`reviewed_on` for exactly that reason.

These tests exercise `registry_stamp()`, the function that decides what frontmatter fields
a crosswalk entry produces -- the same seam `registry_slug()` already used before this fix,
now returning every field the entry justifies rather than only the slug. The crosswalk
itself is substituted via the module's lazy-loaded `_CROSSWALK` cache rather than a file on
disk, the same way `tests/test_enumerate_kpm.py` substitutes an explicit `manifest_path`
for the real corpus manifest.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ingest_kpm as ik  # noqa: E402


class TestRegistryStamp:
    def test_a_reviewed_successor_entry_stamps_basis_and_review_metadata(self, monkeypatch):
        monkeypatch.setattr(ik, "_CROSSWALK", {"mapping": {
            "youth-authority": {
                "slug": "oregon-youth-authority",
                "basis": "successor",
                "note": "renamed across reporting years",
                "reviewed_by": "@morficflux",
                "reviewed_on": "2026-08-01",
            }
        }})
        assert ik.registry_stamp("youth-authority") == {
            "agency_registry_slug": "oregon-youth-authority",
            "agency_registry_corpus": "executive-regulatory-frameworks",
            "agency_registry_basis": "successor",
            "agency_registry_reviewed_by": "@morficflux",
            "agency_registry_reviewed_on": "2026-08-01",
        }

    def test_an_exact_entry_with_no_review_metadata_stamps_no_reviewed_fields(self, monkeypatch):
        monkeypatch.setattr(ik, "_CROSSWALK", {"mapping": {
            "accountancy": {"slug": "oregon-board-of-accountancy", "basis": "exact"},
        }})
        assert ik.registry_stamp("accountancy") == {
            "agency_registry_slug": "oregon-board-of-accountancy",
            "agency_registry_corpus": "executive-regulatory-frameworks",
            "agency_registry_basis": "exact",
        }

    def test_an_unmapped_key_stamps_nothing(self, monkeypatch):
        monkeypatch.setattr(ik, "_CROSSWALK", {"mapping": {}})
        assert ik.registry_stamp("nonexistent") == {}
