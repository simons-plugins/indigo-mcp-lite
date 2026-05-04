"""indigo-mcp-lite — stdlib-only MCP server plugin.

Lifecycle: indexer + MCP handler are wired up in startup;
indexer subscribes to Indigo entity events. MCP requests arrive
via Indigo's IWS as Action callbacks (handle_mcp).
"""

import logging

import indigo  # noqa: F401  (provided by Indigo runtime)


class Plugin(indigo.PluginBase):
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self._apply_log_level()

    def _apply_log_level(self):
        level_name = self.pluginPrefs.get("logLevel", "INFO")
        level = getattr(logging, level_name, logging.INFO)
        self.indigo_log_handler.setLevel(level)
        self.logger.setLevel(level)

    def startup(self):
        self.logger.info("indigo-mcp-lite startup")

    def shutdown(self):
        self.logger.info("indigo-mcp-lite shutdown")

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self._apply_log_level()

    def menuReindexNow(self):
        # Wired in Phase 6
        self.logger.info("Reindex Now: stub (wired in Phase 6)")

    def handle_mcp(self, action, dev=None, callerWaitingForResult=None):
        # Wired in Phase 2
        return {"status": 503, "headers": {"Content-Type": "text/plain"}, "content": "MCP not wired yet"}
