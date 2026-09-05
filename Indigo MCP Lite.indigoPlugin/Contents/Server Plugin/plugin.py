"""indigo-mcp-lite — stdlib-only MCP server plugin.

Lifecycle: MCP handler is constructed in __init__ so it's ready
before any IWS Action callback can fire. MCP requests arrive via
Indigo's IWS as Action callbacks (handle_mcp).
"""

import json
import logging

import indigo  # noqa: F401  (provided by Indigo runtime)
from mcp_handler import MCPHandler


# MCP ``instructions`` (InitializeResult) — the one place the server
# gets to orient a client's model before any tool is called. Tool
# descriptions are read one at a time and only when a tool is already
# being considered; this is read up front, so it carries the routing
# rules that no single description can: which question each family of
# tools answers, and — more importantly — the three places a
# plausible-looking answer is actually incomplete.
SERVER_INSTRUCTIONS = """\
Indigo home automation (indigodomo.com). Tools fall into four groups:

- LOOKUP — what exists: devices, variables, action groups, triggers,
  schedules, folders, plugins. find_devices searches by name/room/type.
- CONTENTS — what an automation DOES and WHEN: get_automation_contents,
  find_automation_references, list_automation_scripts. These decode the
  Indigo database file; the lookup tools cannot see any of it.
- CONTROL — change state: turn on/off, dim, colour, setpoints, execute,
  enable/disable.
- SYSTEM/HISTORY — event log, plugin status, SQL Logger queries.

Three answers that look complete but are not:

1. A schedule's `next_execution` is the next TIMESTAMP, not the rule.
   It cannot distinguish an absolute 06:00 schedule from one tracking
   sunrise, so it cannot show a misconfigured time. For when a schedule
   actually fires, call get_automation_contents(entity_type="schedule")
   and read its `schedule` block.
2. get_trigger_by_id / get_schedule_by_id / get_action_group_by_id
   return METADATA only — name, folder, enabled. They never show the
   action steps or conditions. get_automation_contents does.
3. get_dependencies wraps Indigo's own dependency check, which does NOT
   see devices referenced from inside a plugin action's parameters. It
   can report zero dependents for a device that several action groups
   genuinely drive. find_automation_references DOES see those and
   reports them as `acts_on_via_props`; ask it before concluding
   nothing uses a device.

A plugin action step's target device is sometimes only inside its
`props` (e.g. `dimmer_device_id`), never in `device_id`. Likewise a
device's behaviour often lives in its configuration, not its states —
get_device_by_id returns `plugin_props` for that.

Lighting automation has its own family: `lamplighter_*` reads and
patches the Lamplighter plugin's zone configuration, releases overrides
and locks, switches a zone or the whole plugin, and `lamplighter_explain`
asks the plugin itself why a zone is doing what it is doing (or would
at a given time) rather than inferring it from the event log.
"""


class Plugin(indigo.PluginBase):
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self._apply_log_level()
        self.mcp_handler = MCPHandler(
            logger=self.logger,
            server_name="indigo-mcp-lite",
            server_version=plugin_version,
            instructions=SERVER_INSTRUCTIONS,
        )
        # Built in startup()/_build_history_db(); None = history tools
        # report "not configured".
        self.history_db = None

    def _apply_log_level(self):
        level_name = self.pluginPrefs.get("logLevel", "INFO")
        level = getattr(logging, level_name, logging.INFO)
        self.indigo_log_handler.setLevel(level)
        self.logger.setLevel(level)

    def startup(self):
        self.logger.info("indigo-mcp-lite startup")
        # Deferred imports: keep test imports of `plugin` from pulling
        # in the whole tool tree / indexer at module-load time.
        from indexer import Indexer
        from tool_registry import register_all

        self.indexer = Indexer(indigo_module=indigo, logger=self.logger)
        self.indexer.build()

        # Subscribe to all entity-change streams. Without these, our
        # deviceCreated/Updated/Deleted etc. callbacks never fire and
        # the index goes stale on the first object change.
        indigo.devices.subscribeToChanges()
        indigo.variables.subscribeToChanges()
        indigo.actionGroups.subscribeToChanges()

        self._build_history_db()
        register_all(self.mcp_handler, indigo_module=indigo,
                     indexer=self.indexer,
                     history_db_provider=lambda: self.history_db,
                     logger=self.logger)

    def shutdown(self):
        self.logger.info("indigo-mcp-lite shutdown")

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self._apply_log_level()
            # Rebuild the SQL Logger connection so pref edits take
            # effect without a plugin restart. The history tools hold a
            # provider (not the instance), so the swap is picked up on
            # the next tool call.
            self._build_history_db()

    def _build_history_db(self):
        """(Re)build the SQL Logger reader from prefs.

        ``self.history_db`` stays None when unconfigured (dbType pref
        of "none", the default) — the history tools then return a
        friendly "not configured" error. A failed connection test
        logs but still keeps the instance: transient DB outages
        shouldn't require a plugin restart to recover from."""
        from history_db import HistoryDB

        self.history_db = HistoryDB.from_prefs(self.pluginPrefs, self.logger)

    def menuReindexNow(self):
        """Indigo menu callback: rebuild the FTS5 index from scratch.

        Useful when a user knows they've made bulk changes the
        change-subscription streams might have missed, or when
        debugging stale-index suspicions.
        """
        if not hasattr(self, "indexer") or self.indexer is None:
            self.logger.warning("Reindex Now: indexer not yet ready")
            return
        self.logger.info("Reindex Now: rebuilding entity index")
        self.indexer.build()
        self.logger.info("Reindex Now: complete")

    # ------------------------------------------------------------------
    # Indigo change-subscription callbacks. Each forwards to the indexer
    # after calling super() — the SDK requires the super() call so the
    # default plumbing (state caching, plugin-base bookkeeping) still
    # runs. Static-field short-circuit lives inside the indexer's
    # on_*_updated handlers (see indexer.py).
    # ------------------------------------------------------------------

    def deviceCreated(self, dev):
        super().deviceCreated(dev)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_device_created(dev)

    def deviceUpdated(self, origDev, newDev):
        super().deviceUpdated(origDev, newDev)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_device_updated(newDev)

    def deviceDeleted(self, dev):
        super().deviceDeleted(dev)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_device_deleted(dev)

    def variableCreated(self, var):
        super().variableCreated(var)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_variable_created(var)

    def variableUpdated(self, origVar, newVar):
        super().variableUpdated(origVar, newVar)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_variable_updated(newVar)

    def variableDeleted(self, var):
        super().variableDeleted(var)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_variable_deleted(var)

    def actionGroupCreated(self, actionGroup):
        super().actionGroupCreated(actionGroup)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_action_created(actionGroup)

    def actionGroupUpdated(self, origActionGroup, newActionGroup):
        super().actionGroupUpdated(origActionGroup, newActionGroup)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_action_updated(newActionGroup)

    def actionGroupDeleted(self, actionGroup):
        super().actionGroupDeleted(actionGroup)
        if getattr(self, "indexer", None) is not None:
            self.indexer.on_action_deleted(actionGroup)

    def handle_mcp(self, action, dev=None, callerWaitingForResult=None):
        """IWS entry point for the MCP endpoint at
        ``POST /message/<plugin-id>/mcp``. Extracts HTTP method, headers,
        and body from the incoming action and delegates to MCPHandler,
        which returns the IWS-shaped response dict."""
        if self.mcp_handler is None:
            self.logger.error("MCP endpoint hit before handler ready")
            return {
                "status": 503,
                "headers": {"Content-Type": "application/json"},
                "content": json.dumps({"error": "mcp_not_ready"}),
            }
        http_method = (action.props.get("incoming_request_method") or "POST").upper()
        headers = dict(action.props.get("headers", indigo.Dict()))
        body_raw = action.props.get("request_body", "")
        # IWS hands us bytes for some requests, str for others. Normalise.
        if isinstance(body_raw, (bytes, bytearray)):
            try:
                body = body_raw.decode("utf-8", errors="replace")
            except Exception as exc:
                self.logger.warning(
                    f"MCP request body decode failed, treating as empty: {exc}"
                )
                body = ""
        else:
            body = body_raw or ""
        try:
            return self.mcp_handler.handle_request(http_method, headers, body)
        except Exception as exc:
            # Mirrors the inner handler's discipline at mcp_handler.py
            # (-32603 "Internal error"): keep exception detail in logs,
            # return a fixed message to avoid leaking paths/types.
            self.logger.exception(f"MCP handler raised: {exc}")
            return {
                "status": 500,
                "headers": {"Content-Type": "application/json"},
                "content": json.dumps({"error": "internal_error"}),
            }
