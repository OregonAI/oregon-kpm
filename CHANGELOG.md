# Changelog — Oregon Key Performance Measures — Annual Performance Progress Reports

Keep a Changelog format; ISO dates. Change types: Added, Source-Updated,
Superseded, Repealed, Removed, Verified, Fixed, Security.
Repo-curation dates only — official effective dates live in frontmatter.

## [Unreleased]

### Fixed
- 2026-08-27 — Drift detection was inert: `src/enumerate_kpm.py` hardcoded
  `sha256: ""` on every rebuild, silently erasing whatever
  `corpus-detect-changes --record-baseline` had recorded, and the manifest's
  header note never said which function that baseline is (#43). The
  enumerator now reads the current manifest before rebuilding and carries
  each rediscovered source's recorded `sha256` forward by `id`; a genuinely
  new source still starts empty, and a source dropped from this run's
  carry-forward (e.g. an upstream rename) is now reported by count and id
  rather than going quiet. Measured, not assumed: all 789 manifest entries
  carry a non-empty, 64-hex-char `sha256` (789 of them distinct). Live-fetched
  11 sources spanning 2016-2025 (9 text-layer PDFs, 2 image-only OCR scans)
  and confirmed those 11 recorded values equal
  `corpus_toolkit.repo.content_hash()` — the detector's own function — not
  `hash_snapshot()` (frontmatter `source_sha256`, a different stream that
  agrees with the detector only when BOTH fall back to a raw-byte hash, which
  needs under 200 normalized chars of extracted/OCR'd text — not simply "it's
  a scan": this corpus's six image-only scans all carry committed OCR text
  well over that threshold, and none of the six agree). Verified end-to-end
  against the real, unmodified `corpus-detect-changes` v1.31.1 CLI on a
  bounded 5-source sample: a correct baseline reports 0 changed, a
  deliberately wrong one is still caught, and `--record-baseline` seeds an
  empty one correctly and writes only to the working tree. Added
  `tests/test_enumerate_kpm.py` covering the carry-forward behavior.
  `src/ingest_kpm.py` now also writes this baseline back after each document
  it ingests (`content_hash()` over the just-fetched bytes), so it stays
  current as documents are re-ingested rather than only at the next scheduled
  `--record-baseline` run. Regenerated `_meta/source-manifest.yml` from
  source so its header note carries the corrected contract (previously only
  in this file's prose, never committed to the generated artifact itself),
  and wired a `source-manifest.yml --check` step into `scheduled.yml`'s new
  weekly `source-manifest` job (not the per-PR `generated` job in `ci.yml` —
  checking this file means re-verifying ~830 live upstream URLs, the same
  network cost the `links` job already excludes snapshots/reports from, and
  it can drift with no file in this repo touched, which a per-PR gate can
  never catch regardless of cost) so a future drift between the two is
  caught, not silent. A manifest that exists but fails to parse now aborts
  the enumerator instead of being read as "nothing recorded" (which would
  have re-erased every baseline on the next write — this exact bug,
  reproduced inside the function meant to prevent it).

  Not done here, same as before: the 25 open `source-change` issues (#17-#41)
  stay open and #43 stays open — both require this to reach `main`, which is
  a later phase's job, not this follow-up commit's.
- 2026-08-02 — `llms.txt` `## Contents` was still the template's empty stub — an
  advertised agent entry point serving an empty index (corpus-template#16).
  Filled with annotated entries for `reports/`, the extracted KPM time series,
  the agency crosswalk, the source manifest, and the authority graph. Also
  drops a stray double period from the description line.
