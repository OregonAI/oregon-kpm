"""oregon-kpm#44: the linker stamps `agency_registry_slug` but drops the crosswalk's
`basis` on the floor, so a consumer reading a document's frontmatter cannot tell a
mechanical name match (`basis: exact`) from a human judgement (`alias`/`successor`) --
several of which carry `reviewed_by`/`reviewed_on` for exactly that reason.

These tests exercise `registry_stamp()`, the function that decides what frontmatter fields
a crosswalk entry produces -- reading `_crosswalk_entry()`, the same lookup the slug itself
comes from, and returning every field the entry justifies rather than only the slug. The
crosswalk itself is substituted via the module's lazy-loaded `_CROSSWALK` cache rather than
a file on disk, the same way `tests/test_enumerate_kpm.py` substitutes an explicit
`manifest_path` for the real corpus manifest.
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

    def test_an_entry_with_a_slug_but_no_basis_stamps_no_basis_key(self, monkeypatch):
        """A crosswalk entry missing `basis` must not stamp `agency_registry_basis: null`.

        `null` reads as a value, not as "nothing recorded" -- and check_registry_link_agrees
        would then compare `None != None` and wave it through, so this has to be caught at
        the writer rather than relied on at the guardrail. No entry in this corpus is
        actually missing a basis today (measured: 96/96 carry one), which is exactly why
        this needs a test rather than a live document to catch it.
        """
        monkeypatch.setattr(ik, "_CROSSWALK", {"mapping": {
            "no-basis-key": {"slug": "some-agency"},
        }})
        assert ik.registry_stamp("no-basis-key") == {
            "agency_registry_slug": "some-agency",
            "agency_registry_corpus": "executive-regulatory-frameworks",
        }


class TestRegistryStampFieldsReachTheMcpAllowList:
    def test_every_field_registry_stamp_can_emit_is_in_corpus_yml(self):
        """oregon-kpm#44 review: `agency_registry_basis` etc. were stamped and *verified*
        but the MCP server's `extra_document_fields` allow-list was not updated, so
        `get_document` silently dropped every one of them -- required by our own guardrail
        and unreachable through the corpus's own interface. This reads the allow-list
        rather than hard-coding it, so the NEXT field this module starts stamping is
        checked here too, not just the three known today.
        """
        import yaml
        corpus_yml = Path(__file__).resolve().parent.parent / "_meta" / "corpus.yml"
        allowed = set(yaml.safe_load(corpus_yml.read_text())["mcp"]["extra_document_fields"])
        emittable = {"agency_registry_slug", "agency_registry_corpus"} | {
            field for field, _ in ik.REGISTRY_STAMP_FIELDS}
        missing = emittable - allowed
        assert not missing, (
            f"{missing} can be stamped by registry_stamp() but are not in "
            f"_meta/corpus.yml mcp.extra_document_fields, so the MCP server drops them")
