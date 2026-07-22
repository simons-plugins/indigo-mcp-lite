# Indigo MCP Lite

A stdlib-only [Model Context Protocol](https://modelcontextprotocol.io) server for [Indigo](https://www.indigodomo.com), packaged as an Indigo plugin. Lets AI assistants like Claude search, control, and reason about your Indigo devices through natural language.

> **Why "lite"?** This plugin replaces [`mlamoure/indigo-mcp-server`](https://github.com/mlamoure/indigo-mcp-server) for users who can't run that plugin's vector-search dependencies (LanceDB requires AVX2 + Apple Silicon or recent Intel; on Indigo 2025.2 / Python 3.13 the LanceDB wheels stopped installing on Intel Macs entirely). Lite uses SQLite FTS5 instead — built into Python's stdlib, no native deps, no external API keys, no embeddings. Search is keyword-and-alias rather than semantic, but it's fast (<50ms over ~2k entities) and runs anywhere Indigo runs.

## What it does

Ask Claude things like:

- "How many dimmers do I have in the kitchen?"
- "Find all leak sensors"
- "Turn the bedroom lamp to 30% warm white"
- "What's the heating setpoint for the living room?"
- "Show me errors in the event log from the last hour"
- "Run the goodnight scene"

Lite exposes 33 MCP tools — full read access to devices/variables/action groups, write control of dimmers/relays/thermostats/variables/action groups, system introspection (event log, plugin status), and a single FTS5-backed `find_devices` search.

## Server requirements

- **Indigo Domotics**: 2024.2 or later
- **macOS**: any version Indigo supports — lite has no native dependencies
- **CPU**: Intel or Apple Silicon — no AVX2 requirement, no embeddings library
- **Python**: 3.10+ (Indigo bundles its own; nothing extra to install)
- **Pip dependencies**: zero

This is the whole point of "lite" — install on any Indigo-supported Mac and it works.

## Installation

1. **Download** the latest `Indigo MCP Lite.indigoPlugin.zip` from the [Releases page](https://github.com/simons-plugins/indigo-mcp-lite/releases).
2. **First-time install: double-click** the unzipped `.indigoPlugin` to install via Indigo. Subsequent updates can be `cp`-deployed, but the very first install must go through the Indigo UI so it registers properly.
3. **Enable** the plugin in Indigo: Plugins → Indigo MCP Lite → Enable.
4. **No configuration needed** — lite runs the moment it's enabled. The MCP endpoint is exposed automatically at `https://<your-indigo-host>/message/com.simons-plugins.indigo-mcp-lite/mcp` via Indigo's IWS.

### Authentication

All MCP requests require a Bearer token. Use either:

- **Local secret** for LAN access — set up via `secrets.json` per [Indigo's local secrets docs](https://wiki.indigodomo.com/doku.php?id=indigo_2024.2_documentation:indigo_web_server#local_secrets) and pass as `Authorization: Bearer <local-secret>`.
- **Reflector API key** for remote access — issued from your Indigo Reflector settings.

## Claude Code / Claude Desktop configuration

Two configuration patterns. Pick the one that matches your situation.

### Pattern A — Drop-in replacement (recommended for most users)

Replace mlamoure's `indigo` MCP server entry with lite. Keeps the `mcp__indigo__*` tool namespace and existing muscle memory; best when lite is your only Indigo MCP.

In your Claude Code project's `.mcp.json` (with the dot — Claude Code reads this; not `mcp.json`):

```json
{
  "mcpServers": {
    "indigo": {
      "command": "/opt/homebrew/bin/npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-reflector.indigodomo.net/message/com.simons-plugins.indigo-mcp-lite/mcp",
        "--header",
        "Authorization:Bearer YOUR_TOKEN"
      ],
      "env": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

For Claude Desktop, the equivalent file is `~/Library/Application Support/Claude/claude_desktop_config.json`.

### Pattern B — Side-by-side with mlamoure

Use a distinct key so the tool prefixes don't collide. Tools appear under `mcp__indigo_mcp_lite__*`.

```json
{
  "mcpServers": {
    "indigo": {
      "command": "/opt/homebrew/bin/npx",
      "args": [
        "-y", "mcp-remote",
        "https://your-reflector.indigodomo.net/message/com.vtmikel.mcp_server/mcp/",
        "--header", "Authorization:Bearer YOUR_TOKEN"
      ],
      "env": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    },
    "indigo-mcp-lite": {
      "command": "/opt/homebrew/bin/npx",
      "args": [
        "-y", "mcp-remote",
        "https://your-reflector.indigodomo.net/message/com.simons-plugins.indigo-mcp-lite/mcp",
        "--header", "Authorization:Bearer YOUR_TOKEN"
      ],
      "env": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    }
  }
}
```

Pick this if you want to keep mlamoure's semantic search on Apple Silicon **and** lite's lightweight stdlib path for the simpler queries.

### LAN-only configuration

For local access without the Reflector, swap the URL host:

```
https://jarvis.local:8176/message/com.simons-plugins.indigo-mcp-lite/mcp
```

(Replace `jarvis.local` with your Indigo server's hostname; `8176` is IWS's HTTPS port.) Use your local secret as the Bearer token.

## Important configuration gotchas

These bit us during setup; surfaced here so they don't bite you.

### `.mcp.json` (with the dot) vs `mcp.json` (no dot)

Claude Code reads **`.mcp.json`** (dotfile) for project-local MCP servers. Cowork and some other tools read `mcp.json` (no dot). The two files are independent — editing the wrong one silently does nothing for the other tool. If lite's namespace doesn't appear in Claude Code's available tools after restart, **first check you edited `.mcp.json`** (the dotfile).

### Pin Node via `env.PATH`

`mcp-remote`'s `undici` dependency requires Node ≥ 18. If your shell has an older Node first on PATH (common with `nvm`), the bridge crashes at import with `ReferenceError: ReadableStream is not defined` and Claude Code shows "Server disconnected" with no further detail.

The `env.PATH` block in the configs above pins Homebrew's Node to the front of PATH **for the bridge subprocess only**, sidestepping the issue without changing your shell setup.

### Don't use `type: "http"` in Claude Code's MCP config

Claude Code's `type: "http"` form expects Server-Sent Events (SSE) transport. Lite (and Home Intelligence) speak plain HTTP POST JSON-RPC, not SSE — the connection silently fails at session start. **Use the `mcp-remote` bridge form (`command + args + env`) instead.** `mcp-remote` translates between Claude Code's SSE expectation and the plugin's plain JSON-RPC.

### Cold-start race

If `mcp-remote` isn't yet cached locally, `npx -y mcp-remote ...` has to fetch the package on first run, which can exceed Claude Code's MCP-server-startup timeout. After mcp-remote runs once and is cached, subsequent session starts are fast. If the namespace is missing on the first session, run `npx -y mcp-remote --help` once manually to warm the cache, then start a fresh Claude Code session.

### Restarting lite itself

`restart_plugin` deliberately refuses to restart `com.simons-plugins.indigo-mcp-lite` — a self-restart kills the in-flight MCP request mid-response. To restart lite, use **Indigo's Plugins menu** (Plugins → Indigo MCP Lite → Reload) or another MCP client pointed at a different Indigo plugin.

## Tools

<!-- BEGIN TOOL TABLE -->
<!-- AUTO-GENERATED by scripts/generate_tool_doc.py — 33 tools -->

### Lookup tools (12)

| Tool | Description |
|------|-------------|
| `get_action_group_by_id` | Return a single Indigo action group by id. |
| `get_device_by_id` | Return a single Indigo device by id. |
| `get_devices_by_state` | List Indigo devices matching a state spec. Spec is a JSON object whose keys are state names; values are either literals (==) or string-prefixed comparators (>=, <=, >, <), e.g. {"onState": true, "batteryLevel": "<20"}. |
| `get_devices_by_type` | List Indigo devices filtered by deviceTypeId. |
| `get_variable_by_id` | Return a single Indigo variable by id. |
| `get_zwave_device_details` | Return full Z-Wave metadata for one device: node address, battery level, firmware version, error state, and the raw Z-Wave interface properties (listening/battery flags, manufacturer info) from globalProps. |
| `list_action_groups` | List Indigo action groups with optional pagination. |
| `list_device_folders` | List Indigo device folders (id + name). |
| `list_devices` | List Indigo devices with optional pagination and filters. |
| `list_variable_folders` | List Indigo variable folders (id + name). |
| `list_variables` | List Indigo variables with optional pagination. |
| `list_zwave_devices` | List Z-Wave devices (protocol == ZWave) with node address, battery level, firmware version, and error state. Use get_zwave_device_details for one device's full metadata. |

### Control tools (15)

| Tool | Description |
|------|-------------|
| `action_execute_group` | Execute an Indigo action group by id. |
| `device_set_brightness` | Set a dimmer's brightness 0-100. Accepts a 0-1 float for fractional callers; out-of-range values are clamped. |
| `device_set_hex_color` | Set a colour-capable dimmer's RGB colour from a hex string. Accepts #RRGGBB, RRGGBB, #RGB, or RGB. |
| `device_set_named_color` | Set a colour-capable dimmer's RGB colour from a CSS named colour (red, aliceblue, dodgerblue, …). 140 names supported; case- and space-insensitive; British *grey* spellings resolve to the *gray* equivalents. |
| `device_set_rgb_color` | Set a colour-capable dimmer's RGB colour. Channels are 0-255 byte values; out-of-range values are clamped before being converted to Indigo's 0-100 wire unit. |
| `device_set_rgb_percent` | Set a colour-capable dimmer's RGB colour using 0-100 percent channels (matches Indigo's native unit). |
| `device_set_white_levels` | Set warm/cool white levels and/or colour temperature on an RGBW or dual-white dimmer. All three keys (white, white2, temperature in Kelvin) are individually optional but at least one must be supplied. White levels are 0-100; temperatures are typically 2000-6500K. |
| `device_turn_off` | Turn a device off by id. |
| `device_turn_on` | Turn a device on by id. |
| `thermostat_set_cool_setpoint` | Set a thermostat's cooling setpoint. Temperature units follow the thermostat's own configuration (°F or °C); Indigo handles the conversion. |
| `thermostat_set_fan_mode` | Set a thermostat's fan mode. Accepts friendly names (case-insensitive, spaces/underscores ignored): auto, alwayson (or 'always on' / 'always_on'). |
| `thermostat_set_heat_setpoint` | Set a thermostat's heating setpoint. Temperature units follow the thermostat's own configuration (°F or °C); Indigo handles the conversion. |
| `thermostat_set_hvac_mode` | Set a thermostat's HVAC mode. Accepts friendly names (case-insensitive): off, heat, cool, auto (= heatcool), heatcool, programheat, programcool, programheatcool. |
| `variable_create` | Create a new Indigo variable. Returns the new variable's id, name, value, and folder. Indigo variable values are strings on the wire; numeric/bool callers must serialise themselves before calling. Empty value is permitted. |
| `variable_update` | Update an existing Indigo variable's value. Empty value clears the variable. |

### System tools (5)

| Tool | Description |
|------|-------------|
| `get_plugin_by_id` | Return a single plugin by id with full metadata + live enabled/running/installed flags. Raises if the id is not installed. |
| `get_plugin_status` | Return only the live status flags (enabled/running/installed) for one plugin. Cheaper than get_plugin_by_id for poll-style health checks where the static metadata isn't needed. |
| `list_plugins` | List every installed Indigo plugin with id, display name, version, install path, and live enabled/running/installed flags. Plugins that disappear between enumeration and lookup (rare install/uninstall race) are skipped silently. |
| `query_event_log` | Read entries from Indigo's live event log. ``limit`` (default 50, max 500) caps the number of entries fetched. ``since`` (optional ISO 8601 datetime) filters to entries strictly after that instant — unparseable timestamps are dropped under since-filtering. ``level`` (optional INFO / WARNING / ERROR) approximates severity by matching well-known message prefixes; INFO matches everything by design. |
| `restart_plugin` | Restart an Indigo plugin by id. REFUSES to restart itself (com.simons-plugins.indigo-mcp-lite) — a self-restart would kill the in-flight MCP request mid-response and the caller would see a transport error rather than a clean tool result. Restart this plugin via Indigo's UI instead. |

### Search tools (1)

| Tool | Description |
|------|-------------|
| `find_devices` | Natural-language search over Indigo devices, variables, and action groups via SQLite FTS5. Free-text 'query' matches name + description + folder + type aliases (so 'light' finds dimmers, 'leak' finds leak sensors, etc.). Optional filters: 'room' (folder name, case-insensitive), 'type' (deviceTypeId exact match — only surfaces devices), 'entity_type' (string or list of device/variable/action). Pagination via 'limit' (default 50, max 500) and 'offset'. |
<!-- END TOOL TABLE -->

To regenerate the table from the live registry: `python3 scripts/generate_tool_doc.py`. Single source of truth is `tool_registry.register_all` — adding a tool to any `tools/*.py` module automatically shows up on the next regenerate.

## Search behaviour

`find_devices` indexes every device, variable, and action group at plugin start, then keeps the index live via Indigo's change-subscription callbacks. Static-field changes (renames, folder moves, description edits) trigger a per-row reindex; volatile state changes (brightness, sensor readings, variable value flips) do not — verified at ~1 reindex per minute on a 1,891-entity install.

The FTS5 schema indexes name, description, type label, folder, and a curated list of type aliases (so `"light"` finds anything whose `deviceTypeId` is in the dimmer family even if the device itself isn't named "light"). bm25 weights favour name matches (3.0) over alias matches (2.0) over folder/description (1.0–1.5). The full alias list is in [`type_aliases.py`](Indigo%20MCP%20Lite.indigoPlugin/Contents/Server%20Plugin/type_aliases.py) — adding entries is a one-line change.

## Verifying it works

After configuring Claude, ask:

> "Using indigo-mcp-lite, find all dimmers in the kitchen."

Claude should call `find_devices` with `query="kitchen", type="dimmer"` (or similar) and return the matching devices. If you see a `Server disconnected` error or the namespace doesn't appear, walk through the [configuration gotchas](#important-configuration-gotchas) above.

## Architecture

- **MCP transport**: hand-rolled JSON-RPC 2.0 dispatcher copied verbatim from sister plugin [Home Intelligence](https://github.com/simons-plugins/indigo-home-intelligence) — speaks MCP protocol versions 2025-11-25 and 2025-06-18.
- **Tool surface**: each tool group lives in `tools/<group>.py` and registers itself via `tool_registry.register_all`. Adding a new tool family is a one-file addition.
- **Index**: `indexer.py` maintains an in-memory SQLite FTS5 table over `indigo.devices` / `indigo.variables` / `indigo.actionGroups`, refreshed via change-subscription callbacks on `plugin.py`.
- **Zero pip dependencies in production.** `pytest` is the only test-time dependency.

For the design rationale (FTS5 vs vector search, in-memory vs on-disk, alias-based lexical bridging instead of embeddings), see [`docs/adr/0001-fts5-not-vector-search.md`](docs/adr/0001-fts5-not-vector-search.md) (lands in Phase 8).

## Development

```bash
git clone https://github.com/simons-plugins/indigo-mcp-lite
cd indigo-mcp-lite

# Run the test suite (251 tests, ~1s)
/Library/Frameworks/Python.framework/Versions/3.11/bin/pytest tests/ -q

# Regenerate the README's tool table
python3 scripts/generate_tool_doc.py
```

Contributions welcome — small synonym additions to `type_aliases.py` are especially valuable.

## License

MIT — see [LICENSE](LICENSE).
