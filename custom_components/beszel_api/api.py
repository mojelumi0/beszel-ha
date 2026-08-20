import logging
from typing import Any, Optional

import httpx
from pocketbase import PocketBase
try:
    from pocketbase.core.models import RecordModel
except ImportError:  # pragma: no cover
    RecordModel = Any  # type: ignore[misc,assignment]

LOGGER = logging.getLogger(__name__)


class BeszelApiError(Exception):
    """Base exception for Beszel API errors."""


class BeszelCannotConnect(BeszelApiError):
    """Error to indicate connection problems."""


class BeszelInvalidAuth(BeszelApiError):
    """Error to indicate invalid authentication credentials."""


class BeszelApiClient:
    def __init__(
        self,
        url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: bool = True,
    ) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._client: Optional[PocketBase] = None

    def _ensure_client(self) -> None:
        """Initialize the PocketBase client if not already done."""
        if self._client is None:
            try:
                self._client = PocketBase(self._url, verify=self._verify_ssl)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as err:
                raise BeszelCannotConnect("Unable to connect to Beszel instance") from err
            except ValueError as err:
                raise BeszelApiError("Invalid Beszel URL configuration") from err
            except Exception as err:
                raise BeszelApiError("Failed to initialize PocketBase client") from err

            if self._username and self._password:
                try:
                    self._client.collection("users").auth_with_password(
                        self._username,
                        self._password,
                    )
                except Exception as err:
                    raise BeszelInvalidAuth("Invalid username or password") from err

    def get_systems(self) -> list[RecordModel]:
        try:
            self._ensure_client()
            if self._client is None:
                raise BeszelApiError("Client was not initialized")
            return self._client.collection("systems").get_full_list()
        except BeszelApiError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as err:
            raise BeszelCannotConnect("Unable to fetch systems from Beszel") from err
        except Exception as err:
            raise BeszelApiError("Failed to fetch systems from Beszel") from err

    def get_system_stats(self, system_id: str) -> Optional[RecordModel]:
        """Get the latest system stats for a specific system"""
        try:
            self._ensure_client()
            if self._client is None:
                raise BeszelApiError("Client was not initialized")
            # Get the latest record for the specific system
            records = self._client.collection("system_stats").get_list(
                1, 1, {"filter": f"system = '{system_id}'", "sort": "-created"}
            )
            if records.items:
                return records.items[0]
            return None
        except BeszelApiError as err:
            LOGGER.warning(
                "Failed to fetch stats for system %s: %s",
                system_id,
                err,
                exc_info=True,
            )
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as err:
            LOGGER.warning(
                "Connection problem while fetching stats for system %s: %s",
                system_id,
                err,
                exc_info=True,
            )
            # Return None if no stats found or error occurs
            return None
        except Exception as err:
            LOGGER.warning(
                "Unexpected error while fetching stats for system %s: %s",
                system_id,
                err,
                exc_info=True,
            )
            return None

    def get_smart_devices(self, system_id: Optional[str] = None) -> list[RecordModel]:
        """Get S.M.A.R.T. data for disks"""
        try:
            self._ensure_client()
            if self._client is None:
                raise BeszelApiError("Client was not initialized")
            if system_id:
                # Get devices for specific system
                return self._client.collection("smart_devices").get_full_list(
                    query_params={"filter": f"system = '{system_id}'"}
                )
            else:
                # Get all devices
                return self._client.collection("smart_devices").get_full_list()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as err:
            LOGGER.warning(
                "Connection problem while fetching S.M.A.R.T. devices: %s",
                err,
                exc_info=True,
            )
            return []
        except BeszelApiError as err:
            LOGGER.warning("Failed to fetch S.M.A.R.T. devices: %s", err, exc_info=True)
            return []
        except Exception as err:
            LOGGER.warning(
                "Unexpected error while fetching S.M.A.R.T. devices: %s",
                err,
                exc_info=True,
            )
            return []


class BeszelUpdateApi:
    def __init__(self, api_client: BeszelApiClient) -> None:
        self.api_client = api_client

    @staticmethod
    def _remove_version_prefix(v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.lstrip("v").strip()

    @staticmethod
    def _to_tuple(v: Optional[str]) -> tuple[int, ...]:
        """
        Converts "0.16.1" into a tuple of ints (0,16,1).
        """
        if not v:
            return ()
        parts: list[int] = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except (ValueError, TypeError):
                break
        return tuple(parts)

    @staticmethod
    def _empty_update_info() -> dict[str, Any]:
        return {
            "hub_version": None,
            "latest_version": None,
            "latest_release_url": None,
            "update_available": False,
            "check_update": False,
        }

    def get_update_info(self) -> dict[str, Any]:
        """
        Returns:
          {
            "hub_version": <installed version or None>,
            "latest_version": <latest release tag or None>,
            "latest_release_url": <html_url or None>,
            "update_available": <bool>,
            "check_update": <bool>
          }
        """
        try:
            self.api_client._ensure_client()

            if self.api_client._client is None:
                raise BeszelApiError("Client was not initialized")

            info_res: dict[str, Any] = self.api_client._client.send(
                "/api/beszel/info", {"method": "GET"}
            )
            hub_version = self._remove_version_prefix(info_res.get("v"))
            check_update = info_res.get("cu", False)

            latest_version: Optional[str] = None
            latest_release_url: Optional[str] = None

            if check_update:
                update_res: dict[str, Any] = self.api_client._client.send(
                    "/api/beszel/update", {"method": "GET"}
                )
                latest_version = self._remove_version_prefix(update_res.get("v"))
                latest_release_url = update_res.get("url")

            installed_t = self._to_tuple(hub_version)
            latest_t = self._to_tuple(latest_version)

            update_available = False
            if installed_t and latest_t:
                update_available = latest_t > installed_t
            
            if not update_available:
                latest_version = hub_version

            result = {
                "hub_version": hub_version,
                "latest_version": latest_version,
                "latest_release_url": latest_release_url,
                "update_available": update_available,
                "check_update": check_update,
            }
            return result
        except (BeszelApiError, httpx.HTTPError, ValueError, TypeError) as err:
            LOGGER.error(
                "Error fetching update info from PocketBase API: %s",
                err,
                exc_info=True,
            )
            return self._empty_update_info()
        except Exception as err:
            LOGGER.error(
                "Unexpected error fetching update info from PocketBase API: %s",
                err,
                exc_info=True,
            )
            return self._empty_update_info()