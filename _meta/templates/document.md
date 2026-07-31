---
schema_version: 1
corpus: "oregon-kpm"
jurisdiction: "oregon"
id: 
title: ""
doc_type: 
citation: ""
authority_level: 
issuing_body: ""
legal_authority: []
source_url: ""
source_format: 
retrieved: 
source_sha256: ""     # see the hashing contract note below — CI enforces it
effective_date: 
last_reviewed: 
source_version: ""
status: current
content_mode: verbatim
conversion_notes: ""
last_verified: ""     # EMPTY STRING, never blank: blank parses as null and the
verified_by: ""       # schema types both as string, so a blank fails CI with
                      # "None is not of type 'string'". Empty = not yet verified,
                      # which is true and valid. The human reviewer fills these.
maintainer: ""
relationships:
  implements: []
  implemented_by: []
  references_external: []
  related: []
  supersedes: []
tags: []
---

> **NON-AUTHORITATIVE — AI-friendly reference only.** This is a curated
> copy, not the official text. Verify against the official source:
> {source_url} (retrieved {retrieved}).

# {title} ({citation})

## At a glance

_1–3 sentence plain-language curator summary._

## Full text

_Complete verbatim source text. Everything in this section is diffed
against the pinned snapshot by CI. No paraphrase, no omission beyond what
conversion_notes declares._

## Curator notes

_Optional. Conversion caveats, context. Clearly curator-authored._

## Cross-references

_In-repo links as relative paths. Cross-corpus references go in frontmatter
`relationships` as CITATION STRINGS (e.g. `ORS 192.311`), never as local ids —
the toolkit resolves those against a sibling declared in `corpus.yml`
(`siblings:`). There is no `corpus:id` link syntax; nothing implements one._


<!-- HASHING CONTRACT (CI enforces this; getting it wrong fails
     corpus-verify-provenance with an error that does not explain itself).

     source_sha256 is NOT the hash of the file you downloaded, except in one
     case. The rule, from corpus_toolkit.repo.hash_snapshot:

       - if _meta/snapshots/<snapshot_id>.txt exists AND its
         whitespace-normalized content is >= 200 characters:
             sha256(normalize_ws(<that .txt>))
       - otherwise:
             sha256(raw bytes of _meta/snapshots/<snapshot_id>.<source_format>)

     The text branch exists so the hash is stable across machines: text
     extraction from a PDF varies by tool and version, so the hash is taken over
     the extraction you COMMITTED, once, at ingestion — never re-derived at
     verification time.

     Do not compute this by hand. Call the toolkit:

       from corpus_toolkit.repo import hash_snapshot
       source_sha256 = hash_snapshot(doc_id, source_format, snapshot_dir)

     Also enforced, and not obvious:
       - the filename stem MUST equal the frontmatter `id`
       - `id` must match ^[a-z0-9][a-z0-9-]+$ (lowercase; upstream filenames
         with capitals need lowercasing, and the mapping is worth recording)
       - every line under `## Full text` must appear in the snapshot IN ORDER;
         coverage below 0.70 is a hard failure
       - the disclaimer_marker string from corpus.yml must appear in the body
       - a doc_type may only live in the directory corpus.yml routes it to
-->
