"""Synchronous client for the Beszel PocketBase API."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any, TypeVar

import httpx
from pocketbase import PocketBase
from pocketbase.errors import ClientResponseError
from pocketbase.models import Record

_AUTH_ERROR_STATUSES = {401, 403}
_TRANSIENT_ERROR_STATUSES = {408, 429}
_T = TypeVar("_T")


class BeszelApiError(Exception):
    """Base exception for Beszel API errors."""


class BeszelCannotConnect(BeszelApiError):
    """Error to indicate connection problems."""


class BeszelInvalidAuth(BeszelApiError):
    """Error to indicate invalid authentication credentials."""


class BeszelApiClient:
    """Manage PocketBase authentication and requests for a Beszel Hub."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._client: PocketBase | None = None
        self._auth_lock = RLock()

    def _raise_api_error(
        self,
        err: Exception,
        operation: str,
        *,
        authenticating: bool = False,
    ) -> None:
        """Translate PocketBase and HTTP errors into integration exceptions."""
        if isinstance(err, BeszelApiError):
            raise err

        if isinstance(err, ClientResponseError):
            status = err.status
            if authenticating and status in {400, 401, 403}:
                raise BeszelInvalidAuth("Invalid Beszel username or password") from err
            if status in _AUTH_ERROR_STATUSES:
                raise BeszelInvalidAuth("Beszel rejected the stored credentials") from err
            if status == 0 or status in _TRANSIENT_ERROR_STATUSES or status >= 500:
                raise BeszelCannotConnect(f"Unable to {operation}") from err
            raise BeszelApiError(
                f"Beszel API returned HTTP status {status} while trying to {operation}"
            ) from err

        if isinstance(err, (httpx.HTTPError, OSError)):
            raise BeszelCannotConnect(f"Unable to {operation}") from err
        if isinstance(err, ValueError):
            raise BeszelApiError(f"Invalid data while trying to {operation}") from err
        raise BeszelApiError(f"Unexpected error while trying to {operation}") from err

    def _authenticate(self, client: PocketBase) -> None:
        """Authenticate the PocketBase client with the configured account."""
        if not self._username or not self._password:
            raise BeszelInvalidAuth("Beszel username and password are required")

        try:
            client.collection("users").auth_with_password(
                self._username,
                self._password,
            )
        except Exception as err:  # noqa: BLE001
            # PocketBase can surface model parsing failures in addition to its
            # documented ClientResponseError; translate this API boundary.
            client.auth_store.clear()
            self._raise_api_error(err, "authenticate with Beszel", authenticating=True)

        if not client.auth_store.is_valid:
            client.auth_store.clear()
            raise BeszelInvalidAuth("Beszel returned an invalid authentication token")

    def _ensure_client(self) -> PocketBase:
        """Return an initialized client with a non-expired authentication token."""
        with self._auth_lock:
            if self._client is None:
                try:
                    self._client = PocketBase(self._url, verify=self._verify_ssl)
                except Exception as err:  # noqa: BLE001
                    self._raise_api_error(err, "initialize the Beszel client")

            if not self._client.auth_store.is_valid:
                self._authenticate(self._client)

            return self._client

    def _request(
        self,
        operation: str,
        request: Callable[[PocketBase], _T],
    ) -> _T:
        """Run a request and retry it once after an authentication rejection."""
        client = self._ensure_client()
        token_used = client.auth_store.token

        try:
            return request(client)
        except ClientResponseError as err:
            if err.status not in _AUTH_ERROR_STATUSES:
                self._raise_api_error(err, operation)

            # A token can be revoked before its encoded expiry. Clear only the
            # token used by this request so concurrent requests cannot erase a
            # token that another worker has already refreshed.
            with self._auth_lock:
                if client.auth_store.token == token_used:
                    client.auth_store.clear()

            client = self._ensure_client()
            try:
                return request(client)
            except Exception as retry_err:  # noqa: BLE001
                self._raise_api_error(retry_err, operation)
        except Exception as err:  # noqa: BLE001
            self._raise_api_error(err, operation)

        raise BeszelApiError(f"Unable to {operation}")  # pragma: no cover

    def get_systems(self) -> list[Record]:
        """Return all systems visible to the configured Beszel user."""
        return self._request(
            "fetch systems from Beszel",
            lambda client: client.collection("systems").get_full_list(),
        )

    def get_system_stats(self, system_id: str) -> Record | None:
        """Return the latest statistics record for a system."""

        def _get_stats(client: PocketBase) -> Record | None:
            system_filter = client.filter(
                "system = {:system_id}",
                {"system_id": system_id},
            )
            records = client.collection("system_stats").get_list(
                1,
                1,
                {"filter": system_filter, "sort": "-created"},
            )
            return records.items[0] if records.items else None

        return self._request("fetch system statistics from Beszel", _get_stats)

    def get_smart_devices(self, system_id: str | None = None) -> list[Record]:
        """Return S.M.A.R.T. data, optionally restricted to one system."""

        def _get_devices(client: PocketBase) -> list[Record]:
            query_params: dict[str, str] | None = None
            if system_id:
                query_params = {
                    "filter": client.filter(
                        "system = {:system_id}",
                        {"system_id": system_id},
                    )
                }
            return client.collection("smart_devices").get_full_list(
                query_params=query_params
            )

        return self._request("fetch S.M.A.R.T. devices from Beszel", _get_devices)

    def send(self, path: str) -> Any:
        """Send an authenticated GET request to a Beszel API endpoint."""
        return self._request(
            f"fetch {path} from Beszel",
            lambda client: client.send(path, {"method": "GET"}),
        )

    def close(self) -> None:
        """Close the underlying HTTP client and clear authentication data."""
        with self._auth_lock:
            client = self._client
            self._client = None
            if client is None:
                return
            client.auth_store.clear()
            client.http_client.close()


class BeszelUpdateApi:
    """Read Beszel Hub version and update information."""

    def __init__(self, api_client: BeszelApiClient) -> None:
        self.api_client = api_client

    @staticmethod
    def _remove_version_prefix(version: str | None) -> str | None:
        if not version:
            return None
        return version.lstrip("v").strip()

    @staticmethod
    def _to_tuple(version: str | None) -> tuple[int, ...]:
        """Convert a dotted version such as 0.16.1 into an integer tuple."""
        if not version:
            return ()
        parts: list[int] = []
        for part in version.split("."):
            try:
                parts.append(int(part))
            except (ValueError, TypeError):
                break
        return tuple(parts)

    def get_update_info(self) -> dict[str, Any]:
        """Return installed and latest Beszel Hub version information."""
        info_result = self.api_client.send("/api/beszel/info")
        if not isinstance(info_result, dict):
            raise BeszelApiError("Beszel returned invalid Hub information")

        hub_version = self._remove_version_prefix(info_result.get("v"))
        check_update = bool(info_result.get("cu", False))
        latest_version: str | None = None
        latest_release_url: str | None = None

        if check_update:
            update_result = self.api_client.send("/api/beszel/update")
            if not isinstance(update_result, dict):
                raise BeszelApiError("Beszel returned invalid update information")
            latest_version = self._remove_version_prefix(update_result.get("v"))
            latest_release_url = update_result.get("url")

        update_available = bool(
            self._to_tuple(hub_version)
            and self._to_tuple(latest_version)
            and self._to_tuple(latest_version) > self._to_tuple(hub_version)
        )
        if not update_available:
            latest_version = hub_version

        return {
            "hub_version": hub_version,
            "latest_version": latest_version,
            "latest_release_url": latest_release_url,
            "update_available": update_available,
            "check_update": check_update,
        }
