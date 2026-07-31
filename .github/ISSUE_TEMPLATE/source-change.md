---
name: Source change detected
about: An upstream source's content hash changed
title: "[source-change] "
labels: source-change
assignees: ''
---

<!-- corpus-detect-changes files these automatically. It runs
     `gh issue create --label source-change` WITHOUT checking the exit code, so
     if the `source-change` label does not exist in this repository the command
     fails, the failure is discarded, and the workflow stays green while filing
     nothing. This template exists so the label is created on first use.
     Verify with: gh label list | grep source-change -->

**Source id:**
**URL:**
**Detected:**

## What changed
<!-- Diff summary from the drift report. -->

## Action
- [ ] Re-fetch and re-ingest, or
- [ ] Record why the change needs no ingestion (e.g. cosmetic upstream re-render)
- [ ] Update the affected documents' `retrieved` / `source_sha256`
- [ ] Note it in CHANGELOG.md
