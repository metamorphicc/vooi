from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class McpError(RuntimeError):
    pass


@dataclass
class McpHttpClient:
    url: str
    token_env: str
    timeout_sec: int = 90

    def __post_init__(self) -> None:
        self._next_id = 1
        self._session_id: str | None = None
        self._initialized = False

    @property
    def token(self) -> str:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise McpError(f"Environment variable {self.token_env} is empty")
        return token

    def initialize(self) -> None:
        if self._initialized:
            return
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "vooi-funding-carry-bot", "version": "0.1.0"},
            },
        )
        try:
            self._notify("notifications/initialized", {})
        except McpError:
            # Some MCP servers tolerate a missing initialized notification. The
            # first tool call will fail loudly if this server requires it.
            pass
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._request("tools/list", {})
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.initialize()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if "content" in result:
            return _decode_mcp_content(result["content"])
        return result

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        self._post(body)

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        payload = self._post(body)
        if "error" in payload:
            raise McpError(f"{method} failed: {payload['error']}")
        return payload.get("result", {})

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                raw = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise McpError(f"HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise McpError(f"network timeout after {self.timeout_sec}s") from exc
        except URLError as exc:
            raise McpError(f"network error: {exc.reason}") from exc

        return _parse_http_payload(raw, content_type)


def _parse_http_payload(raw: str, content_type: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    if "text/event-stream" in content_type or raw.startswith("event:"):
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            raise McpError(f"Empty SSE response: {raw[:200]}")
        return json.loads("\n".join(data_lines))
    return json.loads(raw)


def _decode_mcp_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
    if not texts:
        return content
    joined = "\n".join(texts).strip()
    try:
        decoded = json.loads(joined)
        if isinstance(decoded, str):
            try:
                return json.loads(decoded)
            except json.JSONDecodeError:
                return decoded
        return decoded
    except json.JSONDecodeError:
        return joined
