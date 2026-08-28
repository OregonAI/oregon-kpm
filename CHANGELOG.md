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
  new source still starts empty. Measured, not assumed: live-fetched 11
  sources spanning 2016-2025 (9 text-layer PDFs, 2 image-only OCR scans) and
  confirmed all 789 manifest entries' recorded `sha256` already equal
  `corpus_toolkit.repo.content_hash()` — the detector's own function — not
  `hash_snapshot()` (frontmatter `source_sha256`, a different stream that
  agrees with the detector only for scans). Verified end-to-end against the
  real, unmodified `corpus-detect-changes` v1.31.1 CLI on a bounded 5-source
  sample: a correct baseline reports 0 changed, a deliberately wrong one is
  still caught, and `--record-baseline` seeds an empty one correctly and
  writes only to the working tree. Added `tests/test_enumerate_kpm.py`
  covering the carry-forward behavior.
- 2026-08-02 — `llms.txt` `## Contents` was still the template's empty stub — an
  advertised agent entry point serving an empty index (corpus-template#16).
  Filled with annotated entries for `reports/`, the extracted KPM time series,
  the agency crosswalk, the source manifest, and the authority graph. Also
  drops a stray double period from the description line.
