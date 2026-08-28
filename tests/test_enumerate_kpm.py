"""oregon-kpm#43: the enumerator must never erase a recorded drift baseline.

`corpus-detect-changes --record-baseline` is the ONLY thing that is supposed to write a
source's `sha256` in `_meta/source-manifest.yml` -- it is the drift detector's own
baseline, computed by the toolkit's `content_hash()` over freshly fetched bytes (see
`corpus_toolkit.sources.changes`). Before this fix, `src/enumerate_kpm.py` rebuilt the
manifest from scratch on every run and hardcoded `sha256: ""` for every source, silently
erasing whatever `--record-baseline` had recorded. An empty baseline compares unequal to
everything the detector fetches, so every source reads as CHANGED forever -- the same
failure oregon-kpm shipped with the first time (789 changed, 25 filed, 764 dropped).

These tests exercise `build()` at its public seam -- the network calls (`sweep_rss`,
`socrata_rows`, `verify`) are the true external boundary and are faked; everything else
(parsing, id derivation, baseline carry-forward) runs for real. `manifest_path` is an
explicit parameter for exactly this reason: a test must control what "previously
recorded" means without touching the real corpus manifest on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import enumerate_kpm as ek  # noqa: E402


def _manifest_yaml(sources: list[dict]) -> str:
    return yaml.safe_dump({"sources": sources}, sort_keys=False)


class TestLoadRecordedShas:
    def test_no_manifest_on_disk_yet_returns_nothing_to_carry_forward(self, tmp_path):
        missing = tmp_path / "source-manifest.yml"
        assert ek.load_recorded_shas(missing) == {}

    def test_reads_the_recorded_hash_for_each_id_that_has_one(self, tmp_path):
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text(_manifest_yaml([
            {"id": "APPR_BOA_2016-10-07", "sha256": "1615443a" + "0" * 56},
            {"id": "APPR_NEW_2025", "sha256": ""},
        ]))
        recorded = ek.load_recorded_shas(manifest)
        assert recorded == {"APPR_BOA_2016-10-07": "1615443a" + "0" * 56}
        # An empty sha256 is not a recorded baseline -- there is nothing to carry
        # forward for it, and it must not overwrite a later non-empty value with "".
        assert "APPR_NEW_2025" not in recorded

    def test_an_existing_but_unparseable_manifest_raises_rather_than_erasing(self, tmp_path):
        """oregon-kpm#43 review: a manifest that exists but fails to parse must NOT be
        treated the same as a manifest that does not exist yet. Before this fix,
        `load_recorded_shas` swallowed `yaml.YAMLError` and returned `{}` -- which reads
        as "nothing recorded" and would erase all 789 baselines on the next write. The
        file being present means baselines almost certainly ARE recorded; the correct
        response to not being able to read them is to abort, not to guess `{}`."""
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text("sources:\n  - id: foo\n  bad indent: [\n")
        with pytest.raises(yaml.YAMLError):
            ek.load_recorded_shas(manifest)


class TestBuildCarriesBaselinesForward:
    """`build()` with its network edges (sweep/socrata/verify) faked out."""

    def _run(self, monkeypatch, tmp_path, urls: list[str]):
        monkeypatch.setattr(ek, "sweep_rss", lambda: set(urls))
        monkeypatch.setattr(ek, "socrata_rows", lambda: [])
        monkeypatch.setattr(ek.time, "sleep", lambda *_a: None)
        monkeypatch.setattr(ek, "verify", lambda url: (200, "application/pdf", 12345))
        return ek.build(manifest_path=tmp_path / "source-manifest.yml")

    def test_a_rediscovered_source_keeps_its_recorded_baseline(self, monkeypatch, tmp_path):
        url = "https://www.oregonlegislature.gov/lfo/APPR/APPR_BOA_2016-10-07.pdf"
        manifest = tmp_path / "source-manifest.yml"
        recorded_sha = "1615443a" + "0" * 56
        manifest.write_text(_manifest_yaml([
            {"id": "APPR_BOA_2016-10-07", "sha256": recorded_sha},
        ]))

        data = self._run(monkeypatch, tmp_path, [url])

        [src] = data["sources"]
        assert src["id"] == "APPR_BOA_2016-10-07"
        assert src["sha256"] == recorded_sha, (
            "re-enumerating must not clear a baseline corpus-detect-changes "
            "--record-baseline already wrote"
        )

    def test_a_source_new_to_the_manifest_starts_with_no_baseline(self, monkeypatch, tmp_path):
        url = "https://www.oregonlegislature.gov/lfo/APPR/APPR_NEW_2026-01-01.pdf"
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text(_manifest_yaml([]))  # nothing recorded yet

        data = self._run(monkeypatch, tmp_path, [url])

        [src] = data["sources"]
        assert src["id"] == "APPR_NEW_2026-01-01"
        assert src["sha256"] == "", (
            "a genuinely new source has never been baselined -- it must stay empty "
            "until corpus-detect-changes --record-baseline seeds it, not be guessed at"
        )

    def test_only_the_matching_id_is_touched(self, monkeypatch, tmp_path):
        """Two rediscovered sources: each keeps its OWN recorded hash, not each other's."""
        url_a = "https://www.oregonlegislature.gov/lfo/APPR/APPR_BOA_2016-10-07.pdf"
        url_b = "https://www.oregonlegislature.gov/lfo/APPR/APPR_ODF_2016-08-18_KPMs.pdf"
        sha_a, sha_b = "aa" * 32, "bb" * 32
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text(_manifest_yaml([
            {"id": "APPR_BOA_2016-10-07", "sha256": sha_a},
            {"id": "APPR_ODF_2016-08-18_KPMs", "sha256": sha_b},
        ]))

        data = self._run(monkeypatch, tmp_path, [url_a, url_b])

        by_id = {s["id"]: s["sha256"] for s in data["sources"]}
        assert by_id["APPR_BOA_2016-10-07"] == sha_a
        assert by_id["APPR_ODF_2016-08-18_KPMs"] == sha_b

    def test_a_recorded_baseline_with_no_rediscovered_source_is_reported_as_dropped(
            self, monkeypatch, tmp_path, capsys):
        """oregon-kpm#43 review: carry-forward is keyed on `id`, derived from the
        upstream filename -- when a source is renamed or genuinely disappears, its
        recorded baseline has nowhere to land this run. That loss must be printed by
        count and name, not only implied by the "N will be carried forward" line going
        quiet."""
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text(_manifest_yaml([
            {"id": "APPR_BOA_2016-10-07", "sha256": "aa" * 32},
            {"id": "APPR_RENAMED_OLD_2016", "sha256": "bb" * 32},
        ]))
        url = "https://www.oregonlegislature.gov/lfo/APPR/APPR_BOA_2016-10-07.pdf"

        self._run(monkeypatch, tmp_path, [url])

        out = capsys.readouterr().out
        assert "1 recorded baseline(s) had no rediscovered source and were dropped" in out
        assert "APPR_RENAMED_OLD_2016" in out
        assert "APPR_BOA_2016-10-07" not in out.split(
            "had no rediscovered source and were dropped:", 1)[1]

    def test_manifest_header_states_what_sha256_means(self, monkeypatch, tmp_path):
        """oregon-kpm#43 acceptance criterion: the note must name the producing function
        and say it's a different stream than frontmatter `source_sha256`, since the
        manifest itself says 'do not hand-edit' and this is the field's only home for
        that contract."""
        manifest = tmp_path / "source-manifest.yml"
        manifest.write_text(_manifest_yaml([]))

        data = self._run(monkeypatch, tmp_path, [])

        note = data["note"]
        assert "content_hash()" in note
        assert "source_sha256" in note
        assert "record-baseline" in note
