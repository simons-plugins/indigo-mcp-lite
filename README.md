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

Lite exposes 86 MCP tools — full read access to devices/variables/action groups/triggers/schedules, automation contents (decoded action steps, condition trees, and embedded scripts read from the Indigo database file), write control of dimmers/relays/thermostats/variables/action groups, system introspection (event log, plugin status), and a single FTS5-backed `find_devices` search. Device details are enriched with capability flags from a vendored snapshot of the [community device catalog](https://github.com/simons-plugins/indigo-device-catalog) (regenerated per release — no network at runtime), the colour/white control tools use the same data for friendly refusals on unsupported devices, and `list_uncataloged_devices` reports the catalog gaps.

## Server requirements

- **Indigo Domotics**: 2024.2 or later
- **macOS**: any version Indigo supports — lite has no native dependencies
- **CPU**: Intel or Apple Silicon — no AVX2 requirement, no embeddings library
- **Python**: 3.10+ (Indigo bundles its own; nothing extra to install)
- **Pip dependencies**: zero (SQL Logger history works via stdlib sqlite3, or the `psql` CLI for PostgreSQL — no psycopg2)

This is the whole point of "lite" — install on any Indigo-supported Mac and it works.

## Installation

1. **Download** the latest `Indigo MCP Lite.indigoPlugin.zip` from the [Releases page](https://github.com/simons-plugins/indigo-mcp-lite/releases).
2. **First-time install: double-click** the unzipped `.indigoPlugin` to install via Indigo. Subsequent updates can be `cp`-deployed, but the very first install must go through the Indigo UI so it registers properly.
3. **Enable** the plugin in Indigo: Plugins → Indigo MCP Lite → Enable.
4. **No configuration needed** — lite runs the moment it's enabled. The MCP endpoint is exposed automatically at `https://<your-indigo-host>/message/com.simons-plugins.indigo-mcp-lite/mcp` via Indigo's IWS.

### Authentication

Authentication is enforced by **Indigo's Web Server (IWS), in front of the plugin** — the plugin itself performs no Authorization check and never sees your credentials. Requests without a valid Bearer token are rejected by IWS before the MCP endpoint is invoked. Use either:

- **Local secret** for LAN access — set up via `secrets.json` per [Indigo's local secrets docs](https://wiki.indigodomo.com/doku.php?id=indigo_2024.2_documentation:indigo_web_server#local_secrets) and pass as `Authorization: Bearer <local-secret>`.
- **Reflector API key** for remote access — issued from your Indigo Reflector settings.

Consequently, do not expose IWS's port directly to the internet without the local secret configured: with IWS auth disabled or bypassed there is no second line of defence, and every tool — including the control tools — would be reachable unauthenticated.

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

> **Check your npx path first.** The examples use Homebrew's
> `/opt/homebrew/bin/npx`, but Node installed via the nodejs.org installer
> lives in `/usr/local/bin` — even on Apple Silicon. Run `which npx` in a
> terminal and use that path for `command`, and make sure the directory it
> printed is the **first** entry in `env.PATH` (npx's `#!/usr/bin/env node`
> shebang re-resolves `node` via that PATH, so `command` alone isn't
> enough). A wrong `command` path fails silently — the server just shows
> as disconnected with no request ever leaving your machine.

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
<!-- AUTO-GENERATED by scripts/generate_tool_doc.py — 86 tools -->

### Lookup tools (19)

| Tool | Description |
|------|-------------|
| `get_action_group_by_id` | Return a single Indigo action group by id — METADATA only (name, description, folder). It does NOT return the action steps; get_automation_contents(entity_type='action_group') does, and is the only way to tell two similarly named groups apart. |
| `get_dependencies` | List everything that references an entity (triggers, schedules, action groups, control pages, devices, variables). Check this BEFORE variable_delete or device_enable(false) — deleting something with dependents breaks the automations that use it. INCOMPLETE for devices: this wraps Indigo's own dependency check, which does not see a device referenced from inside a plugin action's parameters, so it can report zero dependents for a device several action groups genuinely drive. find_automation_references DOES see those (it reports them as acts_on_via_props) — cross-check there before concluding nothing uses a device. |
| `get_device_by_id` | Return a single Indigo device by id: metadata, subclass fields, runtime `states`, and `plugin_props` — the device's plugin/server CONFIGURATION, keyed by plugin id (what sensors feed an occupancy zone, which action groups a meta relay fires, the server's defaultDimmerLevel). States say what the device IS; plugin_props say what it DOES. |
| `get_device_group` | Resolve a grouped (multi-endpoint) device to its group: member device ids plus the serialised root device. Some properties (battery_level especially) exist only on the root — use this when a child endpoint shows null battery. |
| `get_devices_by_state` | List Indigo devices matching a state spec. Spec is a JSON object whose keys are state names; values are either literals (==) or string-prefixed comparators (>=, <=, >, <), e.g. {"onState": true, "batteryLevel": "<20"}. |
| `get_devices_by_type` | List Indigo devices filtered by deviceTypeId. |
| `get_schedule_by_id` | Return a single Indigo schedule by id — METADATA only, plus next_execution. next_execution is the next TIMESTAMP, not the rule: it cannot show that a schedule named for 19:00 is actually set to 20:00. For WHEN it fires (absolute / sunrise / sunset + offset / countdown, and the day rule) and WHAT it runs, call get_automation_contents(entity_type='schedule'). |
| `get_trigger_by_id` | Return a single Indigo trigger by id — METADATA only (name, folder, enabled, type). It does NOT return what the trigger watches or the actions it runs; get_automation_contents(entity_type='trigger') does. |
| `get_variable_by_id` | Return a single Indigo variable by id. |
| `get_zwave_device_details` | Return full Z-Wave metadata for one device: node address, battery level, firmware version, error state, and the raw Z-Wave interface properties (listening/battery flags, manufacturer info) from globalProps. |
| `list_action_groups` | List Indigo action groups with optional pagination. Names and ids only — for what a group actually does, call get_automation_contents(entity_type='action_group'). |
| `list_device_folders` | List Indigo device folders (id + name). |
| `list_devices` | List Indigo devices with optional pagination and filters. |
| `list_schedules` | List Indigo schedules (id, name, enabled, next_execution) with pagination. Schedules are time-driven automations. next_execution is the next TIMESTAMP, not the firing rule — it cannot tell an absolute 06:00 schedule from one tracking sunrise. For the rule, call get_automation_contents(entity_type='schedule'). |
| `list_triggers` | List Indigo triggers (id, name, type, enabled) with pagination. Triggers are event-driven automations. Metadata only — for what a trigger watches and does, call get_automation_contents(entity_type='trigger'). |
| `list_uncataloged_devices` | List plugin-owned Indigo devices that have no profile in the vendored community device catalog — the gap report for catalog contributions. Built-in/interface devices (no pluginId) are excluded. |
| `list_variable_folders` | List Indigo variable folders (id + name). |
| `list_variables` | List Indigo variables with optional pagination. |
| `list_zwave_devices` | List Z-Wave devices (protocol == ZWave) with node address, battery level, firmware version, and error state. Use get_zwave_device_details for one device's full metadata. |

### Automation contents tools (3)

| Tool | Description |
|------|-------------|
| `find_automation_references` | Reverse index over all schedules/triggers/action groups: which automations reference a device or variable. ONE call is the whole answer — `references` lists EVERY automation that touches it and `roles` says how, so do not filter to a single role or call another tool to complete the picture. Roles: acts_on (a declared action step), acts_on_via_props (a plugin action step naming it only inside its own parameters — `device-id`, `dimmer_device_id`, a comma-separated list — with `matched_props` naming the parameters that matched, so the inference is auditable), condition (in the condition tree), watches (a trigger fires on it). Both acts_on roles cover DIRECT references only: a schedule that runs an action group which acts on the device is reported via the action group, not the schedule — follow execute-action-group results (via get_automation_contents) to trace indirect chains. Indigo's own dependency check (get_dependencies) does NOT see the props case, so it can report zero dependents for a device that several action groups genuinely drive; this tool does see them. If a `props_inference` field is present, that pass did NOT run — a zero result is then not a clear answer, and the field says what to do about it. Pass exactly one of device_id or variable_id. Use before renaming/removing anything, or to answer 'what turns this on?'. |
| `get_automation_contents` | Return WHAT an automation does: decoded action steps (device/variable/plugin/script/HVAC actions, in order) and its condition tree, read from the Indigo database file. entity_type is schedule, trigger, or action_group. Execute-action-group steps are expanded one level. Plugin-action steps carry their parameters in `props` — and note a plugin step's target device is sometimes only in there (e.g. `dimmer_device_id`), so a missing device_id does NOT mean the step is device-less. For a schedule, `schedule` gives WHEN it fires (absolute time / sunrise / sunset + offset / countdown interval, plus the day rule) — the rule itself, which the single next_execution timestamp on get_schedule_by_id cannot convey. Complements get_trigger_by_id / get_schedule_by_id / get_action_group_by_id, which only return metadata. |
| `list_automation_scripts` | List every embedded script across all schedules, triggers, and action groups in the Indigo database, with the owning automation's type/id/name. Sources longer than ~2000 chars are truncated (truncated: true). The audit surface for 'what Python is hiding in my automations?'. |

### Control tools (42)

| Tool | Description |
|------|-------------|
| `action_execute_group` | Execute an Indigo action group by id. |
| `device_beep` | Ask a device to emit an audible beep (hardware permitting) — useful for physically locating it. |
| `device_brighten` | Brighten a dimmer relative to its current level (optional `by` 1-100, optional delay). |
| `device_dim` | Dim a dimmer relative to its current level (optional `by` 1-100, optional delay). |
| `device_enable` | Enable or disable a device's communication (take a flapping/offline device out of service, or bring it back). |
| `device_lock` | Lock a lock device. Optional delay, and duration until it auto-unlocks. |
| `device_ping` | Ping a Z-Wave/Insteon module and report round-trip time in ms — checks reachability without changing state. |
| `device_reset_energy_accum` | Reset a device's accumulated energy (kWh) counter to zero. |
| `device_set_brightness` | Set a dimmer's brightness 0-100. Accepts a 0-1 float for fractional callers; out-of-range values are clamped. |
| `device_set_hex_color` | Set a colour-capable dimmer's RGB colour from a hex string. Accepts #RRGGBB, RRGGBB, #RGB, or RGB. |
| `device_set_named_color` | Set a colour-capable dimmer's RGB colour from a CSS named colour (red, aliceblue, dodgerblue, …). 140 names supported; case- and space-insensitive; British *grey* spellings resolve to the *gray* equivalents. |
| `device_set_rgb_color` | Set a colour-capable dimmer's RGB colour. Channels are 0-255 byte values; out-of-range values are clamped before being converted to Indigo's 0-100 wire unit. |
| `device_set_rgb_percent` | Set a colour-capable dimmer's RGB colour using 0-100 percent channels (matches Indigo's native unit). |
| `device_set_white_levels` | Set warm/cool white levels and/or colour temperature on an RGBW or dual-white dimmer. All three keys (white, white2, temperature in Kelvin) are individually optional but at least one must be supplied. White levels are 0-100; temperatures are typically 2000-6500K. |
| `device_status_request` | Ask the device hardware to refresh its status in Indigo — use when a device's shown state looks stale. |
| `device_toggle` | Toggle a device between on and off. Optional delay and duration (seconds until it toggles back). |
| `device_turn_off` | Turn a device off by id. Optional delay (seconds before) and duration (seconds until it turns back on). |
| `device_turn_on` | Turn a device on by id. Optional delay (seconds before) and duration (seconds until it turns back off). |
| `device_unlock` | Unlock a lock device. Optional delay, and duration until it auto-locks. |
| `schedule_enable` | Enable or disable an Indigo schedule by id. Optional duration fires the OPPOSITE command after N seconds — e.g. disable with duration for a self-rearming holiday mode. |
| `schedule_execute` | Execute a schedule's actions now (out of its normal timing). Conditions honoured by default. |
| `speedcontrol_decrease` | Decrease a speed-control device's speed index (optional `by`). |
| `speedcontrol_increase` | Increase a speed-control device's speed index (optional `by`). |
| `speedcontrol_set_index` | Set a speed-control device (e.g. ceiling fan) to a discrete speed index: 0=off; valid indexes are 0 to speedIndexCount-1 (e.g. a 4-index fan is off/low/medium/high = 0-3). |
| `speedcontrol_set_level` | Set a speed-control device to a 0-100 percent speed level. |
| `sprinkler_next_zone` | Skip to the next zone in the running schedule. |
| `sprinkler_pause` | Pause the currently running sprinkler schedule (resume continues where it left off). |
| `sprinkler_previous_zone` | Go back to the previous zone in the running schedule. |
| `sprinkler_resume` | Resume a paused sprinkler schedule. |
| `sprinkler_run_schedule` | Run a full watering schedule: a list of per-zone durations in MINUTES, one entry per zone in zone order (0 = skip). Zones run sequentially. |
| `sprinkler_run_zone` | Turn on ONE sprinkler zone (1-based index); any active zone stops first. Runs until stopped, another zone starts, or the zone's max duration elapses. |
| `sprinkler_stop` | Stop all watering on a sprinkler device (clears any paused schedule). |
| `thermostat_set_cool_setpoint` | Set a thermostat's cooling setpoint. Temperature units follow the thermostat's own configuration (°F or °C); Indigo handles the conversion. |
| `thermostat_set_fan_mode` | Set a thermostat's fan mode. Accepts friendly names (case-insensitive, spaces/underscores ignored): auto, alwayson (or 'always on' / 'always_on'). |
| `thermostat_set_heat_setpoint` | Set a thermostat's heating setpoint. Temperature units follow the thermostat's own configuration (°F or °C); Indigo handles the conversion. |
| `thermostat_set_hvac_mode` | Set a thermostat's HVAC mode. Accepts friendly names (case-insensitive): off, heat, cool, auto (= heatcool), heatcool, programheat, programcool, programheatcool. |
| `trigger_enable` | Enable or disable an Indigo trigger by id. Optional duration fires the OPPOSITE command after N seconds — e.g. disable with duration auto re-enables (holiday mode). |
| `trigger_execute` | Execute a trigger's actions now. Conditions are honoured by default; set ignore_conditions ONLY for an explicit user-requested override. |
| `variable_create` | Create a new Indigo variable. Returns the new variable's id, name, value, and folder. Indigo variable values are strings on the wire; numeric/bool callers must serialise themselves before calling. Empty value is permitted. |
| `variable_delete` | Delete an Indigo variable by id. PERMANENT — check dependencies (triggers/control pages referencing it) with the user before deleting anything you didn't create. |
| `variable_move_to_folder` | Move a variable into a folder (see list_variable_folders). |
| `variable_update` | Update an existing Indigo variable's value. Empty value clears the variable. |

### History tools (2)

| Tool | Description |
|------|-------------|
| `list_sql_logger_columns` | List the SQL Logger history columns (name + type) recorded for one device — the valid `column` values for query_sql_logger. Errors if the device has no history table. |
| `query_sql_logger` | Query Indigo SQL Logger history for one device + column over a time range. Returns time-bucketed points plus min/max/current. Text columns return the latest string value per bucket and null min/max (no arithmetic on text). Differs from query_event_log (narrated events); SQL Logger is a per-device-column time series. Use find_devices / list_devices for device IDs and list_sql_logger_columns for valid column names. |

### System tools (7)

| Tool | Description |
|------|-------------|
| `get_plugin_by_id` | Return a single plugin by id with full metadata + live enabled/running/installed flags. Raises if the id is not installed. This does NOT show what the plugin can be told to do — use list_plugin_actions(plugin_id) for its declared actions. |
| `get_plugin_status` | Return only the live status flags (enabled/running/installed) for one plugin. Cheaper than get_plugin_by_id for poll-style health checks where the static metadata isn't needed. |
| `list_plugin_actions` | List one plugin's declared Actions.xml actions: id, name, callback method, whether it's hidden (uiPath="hidden" -- programmatic-only, not meant for menus), whether it requires a device, its ConfigUI fields as props (id/type/label/default), and notes (calling guidance pulled from label fields and <Description> elements that isn't itself a prop). An action with NO ConfigUI fields at all is flagged props_undeclared:true rather than reading as taking no arguments -- common for uiPath="hidden" actions, which may still accept props. A field with hidden:true is runtime-computed by the plugin -- don't invent a value for it. A menu field with no enumerable options is flagged values:"dynamic": for indigo.devices / indigo.actionGroups / indigo.variables menus, values_source_tool names the lite tool that lists the real values (list_devices / list_action_groups / list_variables); otherwise values_source is the plugin's own callback, unresolvable cross-plugin. A static menu lists its real values, enforced at dispatch time. Top-level enabled and running are both reported -- they can differ, and a crashed-but-enabled plugin still fails plugin_execute_action. Use plugin_execute_action to actually invoke one. |
| `list_plugins` | List every installed Indigo plugin with id, display name, version, install path, and live enabled/running/installed flags. Plugins that disappear between enumeration and lookup (rare install/uninstall race) are skipped silently. This does NOT show what a plugin can be told to do — use list_plugin_actions(plugin_id) for its declared actions. |
| `plugin_execute_action` | Execute one declared Actions.xml action on another plugin via indigo.server.getPlugin(plugin_id).executeAction(...) -- the sanctioned cross-plugin write surface (Indigo blocks writing another plugin's pluginProps directly). Use list_plugin_actions first to see the real action ids and declared props for this plugin. When the action's fields could be checked (props_validated:true), declared fields you don't supply are fine (defaults/optional) but are listed back in props_not_supplied -- check that field before treating the call as fully configured; the key is absent when props_validated is false, since there was no known field set to compare against. An unrecognized prop key (or an out-of-range value for a static enum field) is refused ONLY when the action declares fields to check against (props_validated:true); one with none declared has no allowlist, so props pass through unchecked -- props_validated:false with a reason then warns that a mistyped prop name is silently dropped by Indigo, not refused. If Actions.xml itself couldn't be read (action_validated:false), the call only proceeds when NO props and NO device_id were given (waitUntilDone is then forced true so Indigo's own InvalidAction can catch a bad action id -- wait_until_done_overridden:true discloses that, with the caller's original request in wait_until_done_requested); otherwise it's refused rather than sending unchecked props/device_id -- and even the one shape that IS attempted has an unknowable third gap: whether the action requires a device at all (device_requirement_unknown_reason), since executeAction still gets called with none either way. device_id is checked for existence AND, against any self-scoped alternative in the action's deviceFilter, that the device actually belongs to this plugin (device_validated in the payload says which); a wrong device has NO Indigo-side backstop, unlike a wrong action id. wait_until_done (default true, and always reflects the value ACTUALLY used -- not the caller's request when overridden) is passed to executeAction's waitUntilDone: true blocks until the action finishes -- a None return then means result:"completed" when verified:true (both props AND device ownership confirmed, or not applicable), or result:"completed_unverified" when verified:false; false means Indigo queued the action without waiting at all (result:"dispatched" on a None return -- weaker still, no completion signal whatsoever, not even an error could ever surface; verified is reported on every branch, including this one). props is a plain object, converted to indigo.Dict internally. A non-None return reports result:"returned" with the value. |
| `query_event_log` | Read entries from Indigo's live event log. ``limit`` (default 50, max 500) caps the number of entries fetched. ``since`` (optional ISO 8601 datetime) filters to entries strictly after that instant — unparseable timestamps are dropped under since-filtering. ``level`` (optional INFO / WARNING / ERROR) approximates severity by matching well-known message prefixes; INFO matches everything by design. |
| `restart_plugin` | Restart an Indigo plugin by id. REFUSES to restart itself (com.simons-plugins.indigo-mcp-lite) — a self-restart would kill the in-flight MCP request mid-response and the caller would see a transport error rather than a clean tool result. Restart this plugin via Indigo's UI instead. |

### Search tools (1)

| Tool | Description |
|------|-------------|
| `find_devices` | Natural-language search over Indigo devices, variables, and action groups via SQLite FTS5. Free-text 'query' matches name + description + folder + type aliases (so 'light' finds dimmers, 'leak' finds leak sensors, etc.). Optional filters: 'room' (folder name, case-insensitive), 'type' (deviceTypeId exact match — only surfaces devices), 'entity_type' (string or list of device/variable/action). Pagination via 'limit' (default 50, max 500) and 'offset'. |

### Auto Lights config tools (4)

| Tool | Description |
|------|-------------|
| `auto_lights_list_zones` | Read the Auto Lights config: every zone with its linked lighting periods, on/off/luminance/presence device ids, luminance and lock/behavior settings, and the device_period_map (per-device brightness overrides per period). This is what the plugin believes as of its last restart, not necessarily what's live right now (e.g. a zone's actual on/off/lock state is separate runtime state, not a config field). Raises if the config file is missing or unreadable — never returns an empty result for a broken read. |
| `auto_lights_reset_locks` | Reset Auto Lights zone lock(s) via the plugin's own reset_all_locks / reset_zone_locks actions. Omit `zone` to reset every zone's lock; pass a zone name to reset just that one — it must be an existing zone name from auto_lights_list_zones, since Auto Lights' own action silently no-ops on an unmatched name. |
| `auto_lights_set_level` | Set one Auto Lights device_period_map cell: how `device` behaves in `zone` during lighting `period`. `level` is an int 1-100 (brightness override), true (include the device at its normal calculated brightness), or false (exclude it from this period). zone/period/device must already exist in the config — call auto_lights_list_zones first to find the right ids/names. Writes the config file, backs it up first (path returned as backup_path), and restarts the Auto Lights plugin so the change takes effect immediately — the plugin only reads this file at startup, so a write with no restart changes nothing observable. If the restart fails this call fails too, even though the file was written; the change is not yet live. Refuses to write (and changes nothing) if the config file was modified on disk since it was read for this call, e.g. by the web editor -- this narrows but does not eliminate a concurrent-write race. WARNING: the required restart reloads Auto Lights' whole config, which resets EVERY zone's lock and in-memory state (timers, failure suppression), not just this zone — see the response's `side_effects`. |
| `auto_lights_set_zone_enabled` | Enable or disable an Auto Lights zone by name (its on/off automation, not the config file — this calls the plugin's own enable_zone/disable_zone action directly). zone must be an existing zone name from auto_lights_list_zones; Auto Lights' own action silently no-ops on an unknown zone name, so this validates first and raises rather than risk that. |

### Lamplighter tools (8)

| Tool | Description |
|------|-------------|
| `lamplighter_explain` | Why one Lamplighter zone is doing what it is doing, computed fresh by the plugin rather than read from a device state. Returns `explain` (one line naming the period, the state and the inputs behind it) and `desired` — a list of {device, name, level} for every light the zone would command. With `at` (local time, YYYY-MM-DDTHH:MM) it is a DRY RUN at that instant instead: which period covers it, what state the machine would be in, and what each light would be told, resolved from the inputs the zone holds now. A dry run decides nothing and writes nothing. This is the tool that replaces reading the plugin's mind from the event log. |
| `lamplighter_get_zone` | One Lamplighter zone in full: its entire configuration block exactly as it sits in lamplighter.json, every state its lamplighter_zone device publishes (state, presence_active/last_seen, lux, dark, period, override device/expiry, desired_summary, counters, and the persisted record), and its live `explain` line. `zone` is the zone NAME from lamplighter_list_zones — Lamplighter has no numeric zone ids. For a dry run at another time, or for the plugin's freshly computed reasoning rather than the last line it published, use lamplighter_explain. |
| `lamplighter_list_zones` | List every Lamplighter lighting zone: its configuration (lights, presence devices, hold_seconds, lux gate, override timing, period names, enabled) joined to its live lamplighter_zone device (id, state, explain, presence_active, period, override device/expiry, daily counters), plus the lamplighter_controller device (the plugin-wide enable, zone counts and config_status). A zone configured with no device shows `device: null` and is listed in `zones_without_device`; a lamplighter_zone device whose zone_name matches no configured zone is listed in `orphan_zone_devices` — both are real problems worth reporting, not empty results. Config fields absent from the file are returned as null rather than resolved to their schema default, so `enabled: null` means "unstated, so true". Raises if the config file is missing or unreadable, or if the Indigo device list cannot be read — never returns an empty zone list for a broken read. |
| `lamplighter_lock_zone` | Hold a Lamplighter zone at whatever its lights are showing right now, exactly as if somebody had moved a dimmer, for the active period's override duration. Release it with lamplighter_reset_override. `zone` must be a configured zone name. Fails (having done nothing) if the zone's override.enabled is false, because Lamplighter would silently decline to lock a zone that never takes overrides. |
| `lamplighter_reconcile_now` | Ask Lamplighter to re-plan every zone and re-command any light that is off its desired level, on the next worker pass. Nothing is forced on: a zone that should be dark stays dark, and an overridden zone keeps its override. Useful after devices have been off the mesh, or after a config edit, to stop waiting for the reconcile tick. |
| `lamplighter_reset_override` | Release a Lamplighter manual override (and any lock left by lamplighter_lock_zone) so the zone resumes automatic control. Omit `zone` to release every zone; pass a zone name to release just that one — it must be a configured zone name from lamplighter_list_zones, since Lamplighter's own action silently no-ops on a name it does not know. Releasing a zone with no override in place is harmless. |
| `lamplighter_set_enabled` | Enable or disable Lamplighter automation. With `zone`, switches that one zone via the plugin's own set_zone_enabled action; the zone keeps its persisted state and its device, it simply stops planning and writing. Without `zone`, switches the plugin-wide enable by turning the lamplighter_controller device on or off — "all automation off" — which leaves every zone's own enable untouched underneath it. A `zone` given must be a configured zone name; Lamplighter's action silently no-ops on an unknown one. Note this is a RUNTIME switch: it does not edit `enabled` in lamplighter.json, so the next config reload restores whatever the file says (use lamplighter_update_zone for a durable change). The plugin-wide form reads the controller back afterwards and reports `enabled_after` plus `applied`: true when the device confirms the requested state, false with `status: not_applied` when it does not (a half-started Lamplighter accepts the command and changes nothing), or null with `status: unconfirmed` when the device could not be re-read — which is unknown, not failed. |
| `lamplighter_update_zone` | Change one Lamplighter zone's configuration by JSON merge patch (RFC 7386) over its config block. A key set to null is REMOVED; nested objects merge; arrays and scalars REPLACE wholesale, so patching `lights` or `periods` replaces the whole array rather than adding to it. If `zone` names no existing zone, `patch` must be a complete zone object and a new zone is created — there the object is taken verbatim, so a null is a stated VALUE (`lux`: null means "this zone has no daylight gate") rather than a deletion. Before anything is written the whole proposed document is sent to Lamplighter's own validator; if it refuses, this call fails with the JSON-pointer path and message it gave and the file on disk is untouched. Also refuses (writing nothing) if the Lamplighter plugin is not enabled — a config that cannot be validated must not be installed — or if the file changed on disk since it was read. On success the previous file is backed up (path returned as `backup`) and the new one written; Lamplighter hot-reloads it within about 5 seconds with overrides and presence carried across, so NO restart is needed and none is performed. `reloaded` is true ONLY on strong evidence the reload happened (the controller's config_loaded_at moving, its zone count changing, or a new zone's device appearing); a change that a reload would explain but does not require — an explain line, a config_status — is reported as `reload_evidence` with `reload_evidence_strength: "weak"` and leaves `reloaded` false. False therefore never means "the plugin ignored it": read `reload_note` (nothing conclusive changed) or `reload_check_skipped` (the check could not be run at all, so nothing is claimed either way) and confirm with lamplighter_get_zone if it matters. |
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

# Run the test suite (~1s)
/Library/Frameworks/Python.framework/Versions/3.11/bin/pytest tests/ -q

# Regenerate the README's tool table
python3 scripts/generate_tool_doc.py
```

Contributions welcome — small synonym additions to `type_aliases.py` are especially valuable.

## License

MIT — see [LICENSE](LICENSE).
