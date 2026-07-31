## What
<!-- Which documents are added/updated and why. Link the source-change or intake issue. -->

## Checklist
- [ ] Source URL reachable; snapshot committed under `_meta/snapshots/`
- [ ] `source_sha256` produced by `corpus_toolkit.repo.hash_snapshot`, not by hand
      (it hashes the whitespace-normalized committed `.txt`, not the file you downloaded)
- [ ] Filename stem equals frontmatter `id`; `id` matches `^[a-z0-9][a-z0-9-]+$`
- [ ] Dates transcribed from the source, never inferred
- [ ] Everything under `## Full text` is verbatim; curator prose confined to
      `## At a glance` / `## Curator notes` / `## Cross-references`
- [ ] Non-authoritative disclaimer present in each document
- [ ] Generated files regenerated and committed (`python3 src/build_graph.py`)
- [ ] `CHANGELOG.md` updated
- [ ] `last_verified` / `verified_by` left empty — the reviewer sets them at approval
