# MCP server for the oregon-kpm corpus (HTTP transport).
#
#   docker build -t oregon-kpm-mcp .
#   docker run -p 8000:8000 oregon-kpm-mcp
#
# The corpus is baked in at build time; rebuild the image to pick up new commits. Mounting
# it instead was tried on executive-regulatory-frameworks and reverted the same day — it
# never shrank the image, and it made the FTS index shared mutable state between the
# deployer and the live container. See platform-deploy's README before repeating it.
#
# BUILD FROM A SHALLOW CLONE, not your working tree. `.git` cannot be excluded — it is a
# RUNTIME dependency, because the FTS cache key is `git rev-parse HEAD` plus a hash of
# `git status --porcelain`, and corpus_overview() shells out to `git log -1`. Without it
# repo_state() collapses to a constant and content changes are never picked up, silently.
#
#   git clone --depth 1 --branch main https://github.com/OregonAI/oregon-kpm build/
#   docker build -t oregon-kpm-mcp build/
#
# THE WORKING-TREE WARNING IS NOT BOILERPLATE FOR THIS CORPUS. `snapshot_policy: hash-only`
# keeps 789 source PDFs — 1.2 GB — out of the repository but NOT out of a developer's
# checkout after an ingest run. `COPY . .` does not consult .gitignore, so a build from a
# working tree would swallow all of it. .dockerignore excludes them regardless; measured,
# that is a 1.2 GB context reduced to 47 MB.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /repo
# Deps BEFORE content, so a content-only change does not re-run pip.
#
# requirements.txt ONLY — never requirements-build.txt. That file pulls PaddleOCR and its
# Paddle runtime, hundreds of MB of wheels used solely to corroborate OCR at ingest time.
# The server never OCRs anything: six documents were OCR'd once, the text is committed, and
# `text_source: ocr` records how it got there. Same split oregon-budget makes for its
# Parquet stack.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Pre-build the FTS index so the first request is instant.
#
# 785 documents, so this is seconds rather than the 8-minute rebuild that shaped ERF's
# deployment. The step earns its place for the OTHER reason: it fails the BUILD if content
# is missing, rather than shipping an image that starts fine, reports healthy, and answers
# nothing.
RUN python3 -c "\
from corpus_toolkit import config as config_mod; \
from corpus_toolkit.mcp.framework import CorpusFramework; \
CorpusFramework(config_mod.load('_meta/corpus.yml')).ensure_index()" \
 && python3 -c "import corpus_toolkit.mcp.server" \
 && corpus-mcp-serve --help >/dev/null
EXPOSE 8000

# --path and --public-hostname both matter behind the tunnel and are easy to omit:
#   * A Cloudflare Tunnel matches on path but does NOT strip it. Routing /oregon-kpm here
#     forwards the whole path, so the server must mount at that same prefix or every
#     request 404s.
#   * Without --public-hostname the SDK's DNS-rebinding guard rejects the forwarded Host
#     header with 421 Invalid Host header.
# Override either at `docker run` for a different hostname or a dedicated-host deployment
# (in which case pass --path /mcp).
CMD ["corpus-mcp-serve", "--config", "_meta/corpus.yml", "--http", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--path", "/oregon-kpm/mcp", \
     "--public-hostname", "oregonai.morficflux.com"]
