# CLAUDE.md — Indigo MCP Lite

> **Part of the [Indigo workspace](../CLAUDE.md)** — see root for cross-project map, standards, and tooling.

## Project Identity

- **Name**: Indigo MCP Lite
- **Type**: Indigo plugin
- **Shortcut**: `mcp lite` / `mcp-lite`
- **GitHub**: https://github.com/simons-plugins/indigo-mcp-lite
- **Plugin ID**: `com.simons-plugins.indigo-mcp-lite`
- **Language**: Python 3.10+

## Role in the workspace

Stdlib-only MCP server plugin. Replaces mlamoure's [`indigomcp`](../indigomcp/) on
Intel Mac under Indigo 2025.2 / Python 3.13, where LanceDB no longer installs and
the upstream embedding-based search is unusable.

Surface: ~28 deterministic tools matching the upstream `indigo-mcp-server` API
(devices, variables, action groups, plugins, event log, thermostat/dimmer/relay
control) plus one novel `find_devices` tool backed by SQLite FTS5 — no
embeddings, no third-party APIs, no network calls beyond what Indigo itself
makes.

Sibling to [`../indigo-home-intelligence/`](../indigo-home-intelligence/) (HI):

- **Lite** = the *general* deterministic Indigo MCP. Read/write the live object
  model, search by name/type/state.
- **HI** = the *home-intelligence-specific* MCP. History queries, weekly digest,
  rule store. Different surface, different lifecycle.

Lite reuses HI's `mcp_handler.py` JSON-RPC core verbatim (Phase 2). The
dispatcher is the same; only the tool registry differs.

## Related projects

- [`../indigo-home-intelligence/`](../indigo-home-intelligence/) — source of
  `mcp_handler.py`. Read its
  `Home Intelligence.indigoPlugin/Contents/Server Plugin/mcp_handler.py` as the
  canonical implementation of the JSON-RPC dispatcher; lite copies it verbatim
  in Phase 2 and only swaps the tool list.
- [`../indigomcp/`](../indigomcp/) — mlamoure's upstream MCP server plugin
  (external author, not a sibling we co-evolve with). What lite replaces on
  Intel Mac under Indigo 2025.2. Used as reference for tool-name parity.

## Standards

Inherits workspace standards from [root CLAUDE.md](../CLAUDE.md#common-standards-apply-to-every-project-unless-its-claudemd-overrides). Key points for this project:

- **Version bump per PR**: `Info.plist` `PluginVersion`. Format `YYYY.R.P`;
  started at `2026.0.1`. Patch (`P`) for fixes/internals, minor (`R`) for
  user-visible features.
- **Testing**: pytest + `pyproject.toml` (pylint with custom Indigo rules,
  120-char lines) — copied verbatim from [`../netro/`](../netro/), the
  workspace reference plugin.
- **Merge**: GitHub PR only, never `--admin`, never squash, wait for CI green,
  wait for user go-ahead.

## Architecture Decision Records

- **Local ADRs**: [`docs/adr/`](./docs/adr/). First ADR (0001) lands in Task 8.1
  and captures the FTS5-vs-vector-search trade-off. See
  [`docs/adr/INDEX.md`](./docs/adr/INDEX.md).
- **Workspace ADRs**: `~/vsCodeProjects/Indigo/docs/adr/`. A workspace-level
  ADR (`0003-intel-mac-mcp-uses-indigo-mcp-lite.md`, Task 8.2) supersedes the
  assumed-mlamoure parts of HI's `docs/adr/0003`.
- **Format**: MADR 4.0.0.

### Rules
- Before introducing a new library or architectural pattern, read
  `docs/adr/INDEX.md` and the workspace INDEX, then grep for relevant ADRs.
- If a new cross-cutting decision is made (e.g. embeddings, network egress),
  propose a workspace ADR before writing code.

---

## Plugin layout

```
Indigo MCP Lite.indigoPlugin/
└── Contents/
    ├── Info.plist                      # metadata, PluginVersion (2026.0.1)
    └── Server Plugin/
        ├── plugin.py                   # lifecycle, runConcurrentThread, menu, HTTP entry
        ├── PluginConfig.xml            # prefs UI
        ├── MenuItems.xml               # menu items (Reindex Now)
        ├── Actions.xml                 # hidden IWS HTTP endpoint (/mcp)
        ├── Devices.xml                 # (empty — no devices)
        ├── mcp_handler.py              # JSON-RPC dispatcher (Phase 2 — copied from HI)
        ├── tool_registry.py            # tool registration table (Phase 3)
        ├── tools/                      # one module per tool group (Phases 3–5)
        ├── indexer.py                  # SQLite FTS5 builder + refresh (Phase 6)
        └── type_aliases.py             # device-type → friendly-name map (Phase 6)
```

Files marked *Phase N* don't exist yet; they land in the phase indicated. The
Phase 1 skeleton is just `plugin.py` + the four XMLs.

## SDK reference

For Indigo plugin lifecycle, IOM constants, device callbacks, etc. — invoke
`/indigo:dev`. Don't inline SDK docs here.

## Cross-references

- **Design doc**: [`/Users/simon/vsCodeProjects/Indigo/docs/plans/2026-05-04-indigo-mcp-lite-design.md`](../docs/plans/2026-05-04-indigo-mcp-lite-design.md)
- **Implementation plan**: [`/Users/simon/vsCodeProjects/Indigo/docs/plans/2026-05-04-indigo-mcp-lite-plan.md`](../docs/plans/2026-05-04-indigo-mcp-lite-plan.md)
- **Local ADR INDEX**: [`docs/adr/INDEX.md`](./docs/adr/INDEX.md)
