from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from yt_player.mcp.config import MEDIA_SERVICE_TIMEOUT_SECONDS, MEDIA_SERVICE_URL


class MediaServiceClient:
    def __init__(
        self,
        base_url: str = MEDIA_SERVICE_URL,
        timeout: float = MEDIA_SERVICE_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", path, json=json, params=params)

    async def patch(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", path, json=json)

    async def delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(method, url, json=json, params=params)
            except httpx.HTTPError as exc:
                raise ToolError(
                    f"Could not reach the media service at {self.base_url}: {exc}. "
                    "Check that it is running and that MEDIA_SERVICE_URL is correct."
                ) from exc

        if response.status_code == 204:
            return {"status": "ok"}

        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolError(
                f"Media service returned non-JSON response: HTTP {response.status_code}"
            ) from exc

        if response.is_error:
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = error.get("code", "MEDIA_SERVICE_ERROR")
                message = error.get("message", "Media service request failed.")
                raise ToolError(f"{code}: {message}")
            raise ToolError(f"Media service request failed: HTTP {response.status_code}")

        return payload
