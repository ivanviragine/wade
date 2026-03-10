"""Thin JSON HTTP client for API-based task providers."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class APIError(Exception):
    """Raised when an HTTP API request fails."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"API error {status_code}: {message}")


class HTTPClient:
    """Minimal JSON API client wrapping httpx.

    Used by API-based providers (ClickUp, Linear, Jira, etc.)
    to avoid duplicating HTTP boilerplate.
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers or {},
            timeout=timeout,
        )

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> HTTPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a GET request and return parsed JSON."""
        resp = self._client.get(path, params=params)
        self._raise_for_status(resp)
        return self._parse_json(resp)

    def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a POST request with JSON body and return parsed JSON."""
        resp = self._client.post(path, json=json)
        self._raise_for_status(resp)
        return self._parse_json(resp)

    def put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a PUT request with JSON body and return parsed JSON."""
        resp = self._client.put(path, json=json)
        self._raise_for_status(resp)
        return self._parse_json(resp)

    def delete(self, path: str) -> None:
        """Send a DELETE request."""
        resp = self._client.delete(path)
        self._raise_for_status(resp)

    def _parse_json(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse JSON from a response, raising APIError on decode failure."""
        try:
            result: dict[str, Any] = resp.json()
        except ValueError:
            raise APIError(resp.status_code, "Invalid JSON response") from None
        return result

    def _raise_for_status(self, resp: httpx.Response) -> None:
        """Raise APIError if the response status indicates failure."""
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
