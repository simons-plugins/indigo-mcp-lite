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
        # Deferred import: keeps test imports of `plugin` from pulling
        # in the whole tool tree at module-load time.
        from tool_registry import register_all
        register_all(self.mcp_handler, indigo_module=indigo)

    def shutdown(self):
        self.logger.info("indigo-mcp-lite shutdown")

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self._apply_log_level()

    def menuReindexNow(self):
        # Wired in Phase 6
        self.logger.info("Reindex Now: stub (wired in Phase 6)")

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
