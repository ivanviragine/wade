"""Tests for the HTTPClient utility."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wade.utils.http import APIError, HTTPClient

# ---------------------------------------------------------------------------
# APIError tests
# ---------------------------------------------------------------------------


class TestAPIError:
    def test_message_format(self) -> None:
        err = APIError(404, "Not Found")
        assert err.status_code == 404
        assert "404" in str(err)
        assert "Not Found" in str(err)


# ---------------------------------------------------------------------------
# HTTPClient tests
# ---------------------------------------------------------------------------


class TestHTTPClient:
    @patch("wade.utils.http.httpx.Client")
    def test_get_success(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": "value"}
        mock_client.get.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        result = client.get("/path", params={"q": "test"})

        assert result == {"key": "value"}
        mock_client.get.assert_called_once_with("/path", params={"q": "test"})

    @patch("wade.utils.http.httpx.Client")
    def test_get_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_client.get.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        with pytest.raises(APIError) as exc_info:
            client.get("/missing")

        assert exc_info.value.status_code == 404

    @patch("wade.utils.http.httpx.Client")
    def test_post_success(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "123"}
        mock_client.post.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        result = client.post("/create", json={"name": "test"})

        assert result == {"id": "123"}

    @patch("wade.utils.http.httpx.Client")
    def test_put_success(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"updated": True}
        mock_client.put.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        result = client.put("/update", json={"field": "value"})

        assert result == {"updated": True}

    @patch("wade.utils.http.httpx.Client")
    def test_delete_success(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.delete.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        client.delete("/remove")

        mock_client.delete.assert_called_once_with("/remove")

    @patch("wade.utils.http.httpx.Client")
    def test_delete_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.delete.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        with pytest.raises(APIError) as exc_info:
            client.delete("/fail")

        assert exc_info.value.status_code == 500

    @patch("wade.utils.http.httpx.Client")
    def test_headers_passed(self, mock_client_cls: MagicMock) -> None:
        HTTPClient(
            base_url="https://api.example.com",
            headers={"Authorization": "Bearer token"},
            timeout=60.0,
        )

        mock_client_cls.assert_called_once_with(
            base_url="https://api.example.com",
            headers={"Authorization": "Bearer token"},
            timeout=60.0,
        )

    @patch("wade.utils.http.httpx.Client")
    def test_close(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        client = HTTPClient(base_url="https://api.example.com")
        client.close()

        mock_client.close.assert_called_once()

    @patch("wade.utils.http.httpx.Client")
    def test_context_manager(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        with HTTPClient(base_url="https://api.example.com") as client:
            assert client is not None

        mock_client.close.assert_called_once()

    @patch("wade.utils.http.httpx.Client")
    def test_invalid_json_response(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_client.get.return_value = mock_resp

        client = HTTPClient(base_url="https://api.example.com")
        with pytest.raises(APIError) as exc_info:
            client.get("/bad-json")

        assert exc_info.value.status_code == 200
        assert "Invalid JSON" in str(exc_info.value)
