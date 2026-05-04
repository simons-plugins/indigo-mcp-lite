"""JSON-RPC 2.0 handler for the MCP endpoint.

Hand-rolled MCP server (no Python MCP SDK). Transport: HTTP POST,
stateless JSON-RPC per request. Runs inside Indigo's IWS action
handler; mlamoure's `indigo-mcp-server` plugin uses the same pattern.

Entry point: `MCPHandler.handle_request(http_method, headers, body)`
returns a dict with ``status`` / ``headers`` / ``content`` keys that
IWS passes straight to the client.

This module is intentionally dependency-free (stdlib only). All data
access happens through collaborators injected at construction time so
the handler can be unit-tested without Indigo.
"""

import json
import logging
import secrets
import time
from typing import Any, Callable, Dict, List, Optional


# MCP spec versions the handler understands (newest first). The
# initialize handshake picks the first one the client also claims.
# Both are live as of Jan 2026 — 2025-11-25 is what current Claude
# Desktop negotiates; 2025-06-18 keeps a grace window for older
# clients still rolling.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")


class MCPHandler:
    """Stateless MCP JSON-RPC 2.0 dispatcher.

    Tools and resources are registered via ``register_tool`` and
    ``register_resource``. Phase 1 ships with no tools yet (they're
    added in a follow-up commit) — this handler on its own speaks
    the handshake and empty-list responses correctly, which is what
    Claude Desktop needs to see before any tool shows up.
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        server_name: str = "indigo-mcp-lite",
        server_version: str = "0.0.0",
    ):
        self.logger = logger or logging.getLogger("Plugin")
        self.server_name = server_name
        self.server_version = server_version

        # Tool / resource registries. Populated by the plugin during
        # startup via register_tool / register_resource.
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._resources: Dict[str, Dict[str, Any]] = {}

        # Lightweight session tracking. Not enforced in Phase 1 (the
        # plugin is single-user and we don't gate tool calls on session
        # presence), but we issue Mcp-Session-Id on initialize so
        # clients that round-trip it see a stable value.
        self._sessions: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Registration API (called from plugin.py at startup)
    # ------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Register a tool callable. ``handler(**arguments) -> Any``;
        the return value is JSON-serialised and wrapped in the
        MCP tool-result envelope. Raising ``ValueError`` / ``TypeError``
        from the handler is mapped to an ``isError: true`` result so
        Claude can self-correct; raising anything else is mapped to a
        JSON-RPC internal error."""
        self._tools[name] = {
            "description": description,
            "inputSchema": input_schema,
            "handler": handler,
        }

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        handler: Callable[[], Any],
        mime_type: str = "text/plain",
    ) -> None:
        """Register a resource callable. ``handler() -> str``; the
        returned string becomes the resource body verbatim."""
        self._resources[uri] = {
            "name": name,
            "description": description,
            "mimeType": mime_type,
            "handler": handler,
        }

    # ------------------------------------------------------------------
    # Request entry point
    # ------------------------------------------------------------------

    def handle_request(
        self,
        http_method: str,
        headers: Dict[str, str],
        body: str,
    ) -> Dict[str, Any]:
        """Top-level IWS handler. Returns a dict with ``status``,
        ``headers``, ``content`` suitable for Indigo IWS action return."""
        # Normalise headers to lowercase — clients vary on case.
        headers_lc = {k.lower(): v for k, v in (headers or {}).items()}

        if (http_method or "").upper() != "POST":
            return _http_response(405, {"Allow": "POST"}, "")

        # MCP HTTP transport sets Accept to application/json (and
        # sometimes text/event-stream). Accept wildcard too — some
        # proxy bridges (notably `mcp-remote`) drop the header.
        accept = headers_lc.get("accept", "")
        if accept and not any(
            token in accept
            for token in ("application/json", "text/event-stream", "*/*")
        ):
            return _http_response(406, {"Content-Type": "text/plain"}, "Not Acceptable")

        # Parse JSON-RPC payload.
        try:
            payload = json.loads(body) if body else None
        except Exception as exc:
            self.logger.debug(f"MCP: JSON parse failed: {exc}")
            return _json_rpc_response(_json_rpc_error(None, -32700, "Parse error"))

        if not payload:
            return _json_rpc_response(_json_rpc_error(None, -32600, "Invalid Request"))

        # The 2025-06-18 spec drops batch support; reject loudly
        # rather than silently processing one message.
        if isinstance(payload, list):
            return _json_rpc_response(
                _json_rpc_error(None, -32600, "Batch requests not supported")
            )

        try:
            response = self._dispatch(payload, headers_lc)
        except Exception as exc:
            self.logger.exception(f"MCP: unhandled dispatch error: {exc}")
            return _json_rpc_response(
                _json_rpc_error(payload.get("id"), -32603, "Internal error")
            )

        # Notifications (no id) get a 200 with an empty body.
        if "id" not in payload:
            return _http_response(
                200,
                {"Content-Type": "application/json; charset=utf-8"},
                "{}",
            )

        extra_headers: Dict[str, str] = {}
        if isinstance(response, dict) and "_mcp_session_id" in response:
            # Surface the session-id header for the client round-trip.
            extra_headers["Mcp-Session-Id"] = response.pop("_mcp_session_id")

        return _http_response(
            200,
            {"Content-Type": "application/json; charset=utf-8", **extra_headers},
            json.dumps(response),
        )

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        msg: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _json_rpc_error(msg.get("id") if isinstance(msg, dict) else None,
                                   -32600, "Invalid Request")

        msg_id = msg.get("id")
        method = msg["method"]
        # Spec: params MUST be Object or Array (or omitted). We only
        # use Object-style params, so reject non-object params with
        # -32602 per JSON-RPC 2.0 rather than trying to .get() a list
        # and crashing with -32603 internal error.
        raw_params = msg.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            return _json_rpc_error(msg_id, -32602, "Invalid params: expected object")
        params = raw_params or {}

        client_ip = (
            headers.get("x-forwarded-for", "").split(",")[0].strip()
            or headers.get("x-real-ip", "")
            or headers.get("remote-addr", "")
            or "unknown"
        )

        if method.startswith("notifications/"):
            # Notifications: log at debug only, always no response.
            self.logger.debug(f"MCP notify: {method} | {client_ip}")
            return None
        elif method in ("tools/call", "resources/read"):
            self.logger.info(f"MCP: {method} | {client_ip}")
        else:
            self.logger.debug(f"MCP: {method} | {client_ip}")

        if method == "initialize":
            return self._handle_initialize(msg_id, params, client_ip)
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return self._handle_tools_list(msg_id)
        if method == "tools/call":
            return self._handle_tools_call(msg_id, params)
        if method == "resources/list":
            return self._handle_resources_list(msg_id)
        if method == "resources/read":
            return self._handle_resources_read(msg_id, params)
        if method == "prompts/list":
            # Phase 1 has no prompts; respond with an empty list so
            # clients that probe the endpoint don't error.
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
        if method == "prompts/get":
            return _json_rpc_error(msg_id, -32602, "Unknown prompt")

        return _json_rpc_error(msg_id, -32601, f"Method not found: {method}")

    # ------------------------------------------------------------------
    # initialize
    # ------------------------------------------------------------------

    def _handle_initialize(
        self,
        msg_id: Any,
        params: Dict[str, Any],
        client_ip: str,
    ) -> Dict[str, Any]:
        requested_version = str(params.get("protocolVersion") or "")
        client_info = params.get("clientInfo", {}) or {}

        if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32602,
                    "message": "Unsupported protocol version",
                    "data": {
                        "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "requested": requested_version,
                    },
                },
            }

        session_id = secrets.token_urlsafe(24)
        self._sessions[session_id] = {
            "created": time.time(),
            "last_seen": time.time(),
            "client_info": client_info,
            "client_ip": client_ip,
            "protocol_version": requested_version,
        }

        client_name = client_info.get("name", "unknown-client")
        self.logger.info(
            f"MCP: initialized client={client_name} ip={client_ip} "
            f"protocol={requested_version} session={session_id[:8]}"
        )

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {
                    "name": self.server_name,
                    "version": self.server_version,
                },
            },
            "_mcp_session_id": session_id,
        }

    # ------------------------------------------------------------------
    # tools/*
    # ------------------------------------------------------------------

    def _handle_tools_list(self, msg_id: Any) -> Dict[str, Any]:
        tools: List[Dict[str, Any]] = [
            {
                "name": name,
                "description": info["description"],
                "inputSchema": info["inputSchema"],
            }
            for name, info in self._tools.items()
        ]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

    def _handle_tools_call(
        self,
        msg_id: Any,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool_name = params.get("name")
        raw_args = params.get("arguments")
        if raw_args is not None and not isinstance(raw_args, dict):
            return _json_rpc_error(
                msg_id, -32602, "Invalid params: arguments must be an object"
            )
        tool_args = raw_args or {}

        if tool_name not in self._tools:
            return _json_rpc_error(msg_id, -32602, f"Unknown tool: {tool_name}")

        handler = self._tools[tool_name]["handler"]
        try:
            result = handler(**tool_args)
        except (TypeError, ValueError) as exc:
            # Input-validation failures are returned as tool-result
            # errors (isError: true) per MCP 2025-11-25 — lets the
            # model self-correct without a protocol-level error.
            self.logger.warning(f"MCP tool {tool_name} validation error: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": str(exc), "tool": tool_name, "success": False}
                            ),
                        }
                    ],
                    "isError": True,
                },
            }
        except Exception as exc:
            # Anything else is an internal fault — the caller should
            # back off, not retry with altered arguments.
            self.logger.exception(f"MCP tool {tool_name} internal error: {exc}")
            return _json_rpc_error(
                msg_id, -32603, f"Tool execution failed: {exc}"
            )

        # Uniform serialisation: dict / list / other → JSON; string →
        # verbatim. Wraps every tool output as a single `text` content
        # block, which is what Claude Desktop expects.
        if isinstance(result, str):
            text_out = result
        else:
            text_out = json.dumps(result, default=str)

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text_out}],
            },
        }

    # ------------------------------------------------------------------
    # resources/*
    # ------------------------------------------------------------------

    def _handle_resources_list(self, msg_id: Any) -> Dict[str, Any]:
        resources = [
            {
                "uri": uri,
                "name": info["name"],
                "description": info["description"],
                "mimeType": info["mimeType"],
            }
            for uri, info in self._resources.items()
        ]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": resources}}

    def _handle_resources_read(
        self,
        msg_id: Any,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        uri = params.get("uri")
        if not uri:
            return _json_rpc_error(msg_id, -32602, "Missing uri parameter")
        if uri not in self._resources:
            return _json_rpc_error(msg_id, -32602, f"Unknown resource: {uri}")

        info = self._resources[uri]
        try:
            body = info["handler"]()
        except Exception as exc:
            self.logger.exception(f"MCP resource {uri} error: {exc}")
            return _json_rpc_error(msg_id, -32603, f"Resource read failed: {exc}")

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": info["mimeType"],
                        "text": body,
                    }
                ]
            },
        }


# ---------------------------------------------------------------------
# Module-level helpers (pure, no dependencies on MCPHandler state)
# ---------------------------------------------------------------------


def _http_response(
    status: int,
    headers: Dict[str, str],
    content: str,
) -> Dict[str, Any]:
    return {"status": status, "headers": headers, "content": content}


def _json_rpc_response(obj: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return _http_response(
        status,
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(obj),
    )


def _json_rpc_error(
    msg_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    }
    if data is not None:
        envelope["error"]["data"] = data
    if msg_id is not None:
        envelope["id"] = msg_id
    return envelope
