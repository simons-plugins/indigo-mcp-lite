---
parent: Decisions
nav_order: 1
title: "ADR-0001: FTS5, not vector search — rely on Claude-side synonym expansion"
status: "accepted"
date: 2026-05-05
decision-makers: solo (Simon)
consulted: none
informed: none
---
# ADR-0001: FTS5, not vector search — rely on Claude-side synonym expansion

## Context and Problem Statement

`indigo-mcp-lite` exists because [`mlamoure/indigo-mcp-server`](https://github.com/mlamoure/indigo-mcp-server) — the established MCP for Indigo — depends on LanceDB for semantic device search, and LanceDB's wheels stopped installing on Intel Macs running Indigo 2025.2 / Python 3.13. We need a stdlib-only equivalent that runs anywhere Indigo runs.

The natural-language `find_devices` tool needs *some* form of semantic recall over the device catalogue (~2,000 entities on a typical install): a query like `"leak sensor"` should surface a device named `"Basement Water Sensor"` even though no token overlaps. Mlamoure achieves this by paying OpenAI for embeddings and storing them in LanceDB. We need a different path.

## Decision Drivers

* **Stdlib-only**: zero pip dependencies in production. SQLite (with FTS5) ships with macOS Python 3.10+. Embedding libraries do not.
* **Runs on Intel Macs**: the entire reason for lite's existence. LanceDB's wheels are native (Apple Silicon + recent Intel + AVX2); SQLite has no such constraint.
* **No external API keys**: mlamoure requires OpenAI. We don't want to introduce that dependency or the per-call cost for users who already pay for Claude.
* **Memory budget**: a 1536-dim embedding × ~2,000 devices × 4 bytes = ~12 MB just for vectors, before any index overhead. FTS5's per-row footprint is bytes, not kilobytes.
* **Search quality**: must be good enough that Claude can satisfy real natural-language queries. "Good enough" defined as: vibe queries ("cosy lights"), pure synonym gaps ("leak"↔"moisture"), and description-implies-purpose queries ("things that warn me about problems") all return useful results.

## Considered Options

1. **SQLite FTS5 (built-in)** — token-and-alias matching with porter stemming. No native deps, no API calls.
2. **`sqlite-vec`** — SQLite extension publishing wheels for macOS Intel + arm64 + Python 3.13. Sub-50 MB. No external API for embeddings (would need a local embedding model — e.g. ~50 MB sentence-transformers).
3. **`lancedb` + OpenAI embeddings** (mlamoure's approach) — high-quality semantic search, but reintroduces the dependencies we forked away from.
4. **`chromadb`, `qdrant`, etc.** — vector databases. Same dependency-weight class as LanceDB, no advantage on Intel Mac compat.

## Decision Outcome

Chosen option: **"SQLite FTS5 (built-in)"**, because it's the only option that delivers stdlib-only semantic-ish search with no native dependencies, no API keys, and a memory footprint measured in megabytes rather than tens of megabytes. The search-quality gap relative to embeddings is real but addressed by **letting Claude do the synonym expansion client-side, before the tool call**:

```
User:   "find anything that detects moisture"
Claude: find_devices(query="leak OR moisture OR water OR damp OR flood OR wet")
FTS5:   returns the basement leak sensor
```

Claude has the same semantic knowledge OpenAI's embeddings encode — there's no reason to pay a server-side service to do an expansion the model on the wire is already doing for free. The tool description tells Claude this is the expected pattern.

The genuine residual gap is **project-specific jargon** Claude has never seen — e.g. a user describing devices in idiosyncratic vocabulary that Claude can't predict. At ~2,000 entities this surface is non-trivial but not painful. Trigger criteria for revisiting are tracked in [issue #1](https://github.com/simons-plugins/indigo-mcp-lite/issues/1).

### Consequences

* Good, because lite installs anywhere Indigo runs — no AVX2 requirement, no Python 3.13 wheel hunt, no native compilation.
* Good, because zero pip dependencies in production. The plugin bundle ships only stdlib.
* Good, because no API keys to configure, no per-call cost, no telemetry to a third party.
* Good, because cold rebuild is fast (~200-400 ms at 2k entities; ~1.5-2 s at 10k) — order of magnitude faster than embedding all device metadata.
* Good, because Claude already does the semantic expansion. No money spent twice.
* Bad, because vibe queries ("cosy lighting") return literal-token matches only — Claude must rewrite them.
* Bad, because project-specific jargon Claude has never seen will miss. Mitigated by the type_aliases.py synonym layer (one-line PR per discovery) and the future user-extensible synonym dict (issue #3).
* Bad, because we can't sort by semantic similarity — only by bm25 token relevance. Acceptable: at the ~2k-entity scale, the top bm25 results are usually the right ones, and Claude's secondary filtering is fast.

### Confirmation

Considered implemented when:

1. `find_devices` is live and serving queries from a 1,891-entity FTS5 index on jarvis.
2. Cold rebuild measured at <500 ms on plugin start.
3. State-update short-circuit verified to hold reindex rate at ≤1/minute under normal Indigo activity.
4. Real-world queries from the smoke test (`"kitchen + dimmer"`, `"leak"`, `"bedroom devices"`) return useful results with bm25-ranked relevance.

All four confirmed in PR #11 (Phase 6 of the implementation plan), 2026-05-05.

## Pros and Cons of the Options

### SQLite FTS5 (chosen)

* Good, because stdlib (`import sqlite3`).
* Good, because porter tokenizer gives stemming for free (lights/light/lighting).
* Good, because alias columns + bm25 column weighting let us tune precision/recall per field without touching index code.
* Good, because the entire data structure fits in `:memory:` — no I/O during search.
* Bad, because no semantic similarity. Claude compensates.
* Bad, because token-based search misses long-tail synonyms not in the alias dict.

### sqlite-vec

* Good, because hybrid lexical+vector search would close the residual semantic gap.
* Good, because still no native API requirement (could use a local model).
* Bad, because adds a pip-install dependency (or bundled wheel).
* Bad, because requires an embedding model — either bundled (~50-100 MB binary footprint) or a network call to OpenAI/Anthropic (reintroduces API keys + cost).
* Bad, because increases plugin install complexity for the small subset of users hitting the long-tail synonym gap.
* **Stays available as the upgrade hatch** if FTS5 proves insufficient — see issue #1.

### lancedb + OpenAI embeddings (rejected; this is what we forked away from)

* Good, because high-quality semantic search.
* Bad, because doesn't install on Intel Mac under Indigo 2025.2 / Python 3.13 — the bug we're forking around.
* Bad, because requires OpenAI API key and per-call cost.
* Bad, because heavy native dependency for what amounts to "find the kitchen lamps".

### chromadb / qdrant / pgvector

* Good, because would also deliver vector search.
* Bad, because all heavier than `sqlite-vec` for the same fundamental capability.
* Bad, because none of them dodge the "needs an embedding source" sub-problem — same OpenAI/local-model bind as above.

## More Information

* Workspace design doc §7 (FTS5 schema + indexing lifecycle) and §8 (this trade-off in long form): `~/vsCodeProjects/Indigo/docs/plans/2026-05-04-indigo-mcp-lite-design.md`.
* Workspace ADR-0003: `docs/adr/0003-intel-mac-mcp-uses-indigo-mcp-lite.md` — workspace-level decision that lite is the canonical Intel-Mac MCP, supersedes the assumed-mlamoure parts of HI's ADR-0003.
* Issue #1 (sqlite-vec upgrade hatch): https://github.com/simons-plugins/indigo-mcp-lite/issues/1
* Issue #3 (user-extensible synonym dict): https://github.com/simons-plugins/indigo-mcp-lite/issues/3
* Mlamoure's plugin (the reference for what we're replacing): https://github.com/mlamoure/indigo-mcp-server

## For AI agents

- DO: rewrite natural-language queries client-side before calling `find_devices` — expand synonyms, add OR alternates, scope to a folder when the user mentions a room. The tool description tells Claude this is the pattern; honour it.
- DO: file a one-line PR against `type_aliases.py` whenever a real user query misses because of an unexpanded jargon term. Growing the alias dict is the cheapest improvement.
- DON'T: introduce embedding libraries, sentence transformers, or external API calls in `find_devices`. Issue #1 is the deliberate path if FTS5 proves insufficient — it's not a casual addition.
- DON'T: replace bm25 weights with raw token frequency — the weighted column ordering (name=3 > aliases=2 > folder=1.5) is load-bearing for query quality.
