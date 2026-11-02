---
parent: Decisions
nav_order: 3
title: "ADR-0003: On Indigo 2026.1+, lite registers only its extension tools; the server's built-in MCP is the general surface"
status: "accepted"
date: 2026-11-02
decision-makers: solo (Simon)
consulted: none
informed: none
supersedes: none
superseded_by: none
---
# ADR-0003: On Indigo 2026.1+, lite registers only its extension tools; the server's built-in MCP is the general surface

## Context and Problem Statement

Indigo 2026.1 ships an MCP server inside the Indigo Web Server at
`/v3/api/mcp`. It speaks Streamable HTTP, takes the same bearer tokens as
the rest of IWS, works on the LAN and over the Reflector, and covers lookup
and control: listing and reading devices, variables, action groups,
triggers, schedules and plugins; on/off, dimming, colour, thermostats,
sprinklers, speed controls; executing action groups, triggers and
schedules; creating and updating variables; the event log; plugin status
and restarts. Its execute, lock and plugin-restart calls are
confirmation-gated: they return a token that must be passed to its
`confirm` tool.

53 of lite's 86 tools duplicate that surface. Registering both costs every
Claude session the duplicate tool definitions, and lite's duplicate control
tools are an ungated path around the built-in's confirmation step.

What does lite register on a server that has its own MCP?

## Decision Drivers

* One general Indigo MCP per install, and the first-party one when it
  exists: it is maintained with the server and confirm-gated.
* Every registered tool is paid for on every turn.
* Lite's lasting value is what the built-in does not do: decoding
  automations from the database file, SQL Logger history, cross-plugin
  actions, Lamplighter and Auto Lights configuration, Z-Wave detail,
  enable/disable, variable delete/move, and diagnostics.
* Lite is public, and servers before 2026.1 have no built-in MCP at all.
* The `tools/*.py` modules should not need to know about any of this.

## Considered Options

1. **Gate on server version**: on 2026.1+ drop the 53 covered tools, with a
   plugin preference to keep them.
2. Hard-delete the 53 tools in a major release.
3. Keep registering everything and let users run both servers.
4. Keep lite's lookup tools (for objects the built-in cannot see), drop the
   rest.

## Decision Outcome

Chosen option: **1, gate on server version**.

* `tool_registry.server_has_builtin_mcp` reads `indigo.server.version`;
  2026.1 or later means the built-in MCP is present. Anything unreadable or
  unparsable answers "no", and lite registers everything (registering too
  much is recoverable, too little is not), with a warning naming what it
  saw.
* On such a server, `register_all` does not register the names in
  `BUILTIN_COVERED` (the tools marked † in the README's tool table), and
  `plugin.py` does not build the FTS5 index, since its only consumer,
  `find_devices`, is among them. A call to a dropped name gets an error
  saying the built-in server provides it, rather than "Unknown tool".
* The **Legacy tools** plugin preference (`legacyTools`, default off)
  restores the full set. It is read once at plugin start, together with the
  version, so the tool set and the `instructions` text always agree; a
  change takes effect on restart.
* The `instructions` text on a slim server sends clients to `/v3/api/mcp`
  for lookup and control and describes only lite's extension families.
* The gate is a proxy around the handler inside `register_all`; the tool
  modules are untouched.

### Accepted losses

* **Visibility.** The built-in server sees only objects with Remote Display
  turned on, on the object and its folder. Lite could see everything.
  Accepted: objects the AI should reach get Remote Display turned on, which
  is the product's own contract. The preference restores lite's view for
  anyone who disagrees.
* **Two capabilities** have no built-in equivalent:
  `device_set_white_levels`'s second white channel (`white2`) and
  multi-key `get_devices_by_state` (the built-in takes one key and an
  operator). Both are niche; the preference restores them.
* **Locks.** Locking on the built-in is expected to be `turn_on`/`turn_off`
  on a lock-subtype device, behind a confirm token. Not yet verified on real
  lock hardware; until it is, the preference restores `device_lock` /
  `device_unlock`.

### Consequences

* Good, because a 2026.1 install pays for 33 lite tools per session instead
  of 86, with no duplicates.
* Good, because control and execution go through the built-in's
  confirmation gate.
* Good, because lite's remit is crisp: what the built-in does not do. New
  lite tools for 2026.1+ servers must stay inside it.
* Good, because servers before 2026.1 are unaffected.
* Bad, because the 53 gated tools stay in the codebase, tested, for older
  servers. Hard deletion is a later decision, once those are rare, and needs
  its own ADR.
* Bad, because unpublished objects are invisible to the AI on a slim server
  unless the preference is on.

### Confirmation

* `tests/test_tool_registry.py` pins the 53 / 33 split against a 2026.1 and a
  2025.2 mock server, the legacy-preference and explicit-decision paths, the
  version-parsing boundaries, and the relocation message.
* `tests/test_plugin_builtin_mcp_gate.py` makes the indexer and the change
  subscriptions fatal on a slim server and checks the startup log line.
* On a real 2026.1 server, `tools/list` on lite's endpoint returns 33 tools.

## More Information

* This follows the workspace-level decision record, ADR-0009 in Simon's
  private Indigo workspace, which also moves the `indigo` client key to the
  built-in server and withdraws lite's tool-name parity promise with
  mlamoure's plugin. That record and its tool-by-tool overlap analysis are
  not published; this ADR is the public statement of the decision for this
  repository.
* Built-in server docs: https://docs.indigodomo.com/2026.1/api/mcp/ and
  https://docs.indigodomo.com/2026.1/user/mcp-control/connecting/
