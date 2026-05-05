"""indigo-mcp-lite — stdlib-only MCP server plugin.

Lifecycle: MCP handler is constructed in __init__ so it's ready
before any IWS Action callback can fire. MCP requests arrive via
Indigo's IWS as Action callbacks (handle_mcp).
"""

import json
import logging

import indigo  # noqa: F401  (provided by Indigo runtime)
from mcp_handler import MCPHandler


class Plugin(indigo.PluginBase):
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self._apply_log_level()
        self.mcp_handler = MCPHandler(
            logger=self.logger,
            server_name="indigo-mcp-lite",
            server_version=plugin_version,
        )

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

        register_all(self.mcp_handler, indigo_module=indigo,
                     indexer=self.indexer)

    def shutdown(self):
        self.logger.info("indigo-mcp-lite shutdown")

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self._apply_log_level()

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
            except Exception:
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
