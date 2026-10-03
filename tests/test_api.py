"""Tests for the synchronous Beszel API client."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pocketbase.errors import ClientResponseError

API_PATH = (
    Path(__file__).parents[1] / "custom_components" / "beszel_api" / "api.py"
)
API_SPEC = importlib.util.spec_from_file_location("beszel_api_standalone", API_PATH)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)

BeszelApiClient = api.BeszelApiClient
BeszelCannotConnect = api.BeszelCannotConnect
BeszelInvalidAuth = api.BeszelInvalidAuth
BeszelUpdateApi = api.BeszelUpdateApi


class FakeAuthStore:
    """Small mutable auth store used by the PocketBase mock."""

    def __init__(self) -> None:
        self.token = ""
        self.is_valid = False
        self.clear_calls = 0

    def clear(self) -> None:
        self.token = ""
        self.is_valid = False
        self.clear_calls += 1


def _mock_pocketbase() -> tuple[MagicMock, MagicMock, MagicMock]:
    pocketbase = MagicMock()
    pocketbase.auth_store = FakeAuthStore()
    users = MagicMock()
    systems = MagicMock()

    def collection(name: str):
        return users if name == "users" else systems

    pocketbase.collection.side_effect = collection
    return pocketbase, users, systems


def _successful_auth(pocketbase: MagicMock):
    def authenticate(*_args, **_kwargs):
        pocketbase.auth_store.token = "valid-token"
        pocketbase.auth_store.is_valid = True

    return authenticate


def test_connection_error_during_authentication_is_classified() -> None:
    """PocketBase status-zero errors must become transient connection errors."""
    pocketbase, users, _systems = _mock_pocketbase()
    users.auth_with_password.side_effect = ClientResponseError(
        "transport failed",
        original_error=httpx.ConnectError("offline"),
    )

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        with pytest.raises(BeszelCannotConnect):
            client.get_systems()


def test_invalid_password_is_classified() -> None:
    """A PocketBase 400 from auth-with-password is invalid credentials."""
    pocketbase, users, _systems = _mock_pocketbase()
    users.auth_with_password.side_effect = ClientResponseError("bad login", status=400)

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "wrong")
        with pytest.raises(BeszelInvalidAuth):
            client.get_systems()


def test_expired_token_authenticates_before_request() -> None:
    """An absent or expired token must be refreshed before data is requested."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    systems.get_full_list.return_value = [SimpleNamespace(id="system-1")]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"

    users.auth_with_password.assert_called_once_with("user", "password")


def test_revoked_token_reauthenticates_and_retries_once() -> None:
    """A still-unexpired but rejected token must not leave the client stuck."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    systems.get_full_list.side_effect = [
        ClientResponseError("revoked", status=401),
        [SimpleNamespace(id="system-1")],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"

    assert users.auth_with_password.call_count == 2
    assert systems.get_full_list.call_count == 2


def test_empty_system_list_reauthenticates_revoked_session() -> None:
    """A silent PocketBase list-rule rejection must trigger reauthentication."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    users.auth_refresh.side_effect = ClientResponseError("revoked", status=401)
    systems.get_full_list.side_effect = [
        [SimpleNamespace(id="system-1")],
        [],
        [SimpleNamespace(id="system-1")],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"
        assert client.get_systems()[0].id == "system-1"

    users.auth_refresh.assert_called_once_with()
    assert users.auth_with_password.call_count == 2
    assert systems.get_full_list.call_count == 3


def test_valid_empty_system_list_is_not_repeatedly_refreshed() -> None:
    """Removing all assigned systems must remain a valid empty result."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    systems.get_full_list.side_effect = [
        [SimpleNamespace(id="system-1")],
        [],
        [],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"
        assert client.get_systems() == []
        assert client.get_systems() == []

    users.auth_refresh.assert_called_once_with()
    users.auth_with_password.assert_called_once_with("user", "password")
    assert systems.get_full_list.call_count == 3


def test_empty_system_list_after_reauth_does_not_loop() -> None:
    """A valid empty result after reauthentication must stop further retries."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    users.auth_refresh.side_effect = ClientResponseError("revoked", status=401)
    systems.get_full_list.side_effect = [
        [SimpleNamespace(id="system-1")],
        [],
        [],
        [],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"
        assert client.get_systems() == []
        assert client.get_systems() == []

    users.auth_refresh.assert_called_once_with()
    assert users.auth_with_password.call_count == 2
    assert systems.get_full_list.call_count == 4


def test_empty_system_auth_refresh_connection_error_is_classified() -> None:
    """A failed session check must not masquerade as a valid empty account."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    users.auth_refresh.side_effect = ClientResponseError(
        "transport failed",
        original_error=httpx.ConnectError("offline"),
    )
    systems.get_full_list.side_effect = [
        [SimpleNamespace(id="system-1")],
        [],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"
        with pytest.raises(BeszelCannotConnect):
            client.get_systems()

    users.auth_refresh.assert_called_once_with()
    users.auth_with_password.assert_called_once_with("user", "password")


def test_empty_system_reauth_rejects_invalid_credentials() -> None:
    """Rejected replacement credentials must start Home Assistant reauth."""
    pocketbase, users, systems = _mock_pocketbase()
    auth_calls = 0

    def authenticate(*_args, **_kwargs):
        nonlocal auth_calls
        auth_calls += 1
        if auth_calls == 1:
            pocketbase.auth_store.token = "valid-token"
            pocketbase.auth_store.is_valid = True
            return
        raise ClientResponseError("bad login", status=400)

    users.auth_with_password.side_effect = authenticate
    users.auth_refresh.side_effect = ClientResponseError("revoked", status=401)
    systems.get_full_list.side_effect = [
        [SimpleNamespace(id="system-1")],
        [],
    ]

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_systems()[0].id == "system-1"
        with pytest.raises(BeszelInvalidAuth):
            client.get_systems()

    users.auth_refresh.assert_called_once_with()
    assert users.auth_with_password.call_count == 2


def test_system_filter_uses_pocketbase_escaping() -> None:
    """System IDs must go through PocketBase's parameterized filter builder."""
    pocketbase, users, stats_service = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    pocketbase.filter.return_value = "safe-filter"
    stats_service.get_list.return_value = SimpleNamespace(items=[])

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        assert client.get_system_stats("system-'id") is None

    pocketbase.filter.assert_called_once_with(
        "system = {:system_id}",
        {"system_id": "system-'id"},
    )
    stats_service.get_list.assert_called_once_with(
        1,
        1,
        {"filter": "safe-filter", "sort": "-created"},
    )


def test_close_releases_http_client() -> None:
    """Unloading must release PocketBase's synchronous HTTP client."""
    pocketbase, users, systems = _mock_pocketbase()
    users.auth_with_password.side_effect = _successful_auth(pocketbase)
    systems.get_full_list.return_value = []

    with patch.object(api, "PocketBase", return_value=pocketbase):
        client = BeszelApiClient("https://beszel.example", "user", "password")
        client.get_systems()
        client.close()

    pocketbase.http_client.close.assert_called_once_with()
    assert client._client is None


def test_update_information() -> None:
    """Hub version comparison should normalize an optional v prefix."""
    client = MagicMock()
    client.send.side_effect = [
        {"v": "v0.12.0", "cu": True},
        {"v": "v0.13.0", "url": "https://example.test/release"},
    ]

    result = BeszelUpdateApi(client).get_update_info()

    assert result == {
        "hub_version": "0.12.0",
        "latest_version": "0.13.0",
        "latest_release_url": "https://example.test/release",
        "update_available": True,
        "check_update": True,
    }
