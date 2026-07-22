"""Registration and token management for the reMarkable Cloud API.

reMarkable's cloud API is unofficial and undocumented; this implementation
follows the auth flow used by long-running open-source clients such as
`rmapi` and `rmapy`:

1. The user generates a one-time, single-use pairing code at
   https://my.remarkable.com/device/browser/connect
2. That code is exchanged for a long-lived *device token*.
3. The device token is exchanged for a short-lived *user token* (~1 hour)
   whenever one is needed. The device token itself does not expire from
   normal use.

Both exchange endpoints return the raw JWT as the response body (not JSON).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .exceptions import NotRegisteredError, RegistrationError, TokenRefreshError

logger = logging.getLogger("rmpush.auth")

AUTH_BASE = "https://webapp-production-dot-remarkable-production.appspot.com"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/token/json/2/device/new"
USER_TOKEN_URL = f"{AUTH_BASE}/token/json/2/user/new"

# Must be one of the values the reMarkable backend recognizes.
DEVICE_DESC = "desktop-linux"

DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "rmpush" / "credentials.json"

# Refresh the user token this many seconds before it actually expires.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


def _decode_jwt_payload(token: str) -> dict:
    """Best-effort, unverified decode of a JWT payload (we trust our own token)."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload)
    except Exception:
        return {}


@dataclass
class Credentials:
    device_token: Optional[str] = None
    device_id: Optional[str] = None
    user_token: Optional[str] = None

    @classmethod
    def load(cls, path: Path) -> "Credentials":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(
            device_token=data.get("device_token"),
            device_id=data.get("device_id"),
            user_token=data.get("user_token"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "device_token": self.device_token,
                    "device_id": self.device_id,
                    "user_token": self.user_token,
                },
                indent=2,
            )
        )
        path.chmod(0o600)


class Auth:
    """Handles device registration and user token refresh, with on-disk caching."""

    def __init__(
        self,
        credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
        session: Optional[requests.Session] = None,
    ):
        self.credentials_path = Path(credentials_path)
        self.session = session or requests.Session()
        self.credentials = Credentials.load(self.credentials_path)

    @property
    def is_registered(self) -> bool:
        return bool(self.credentials.device_token)

    def register(self, one_time_code: str) -> str:
        """Exchange a one-time pairing code for a device token and persist it.

        Get a code from https://my.remarkable.com/device/browser/connect
        (valid for a few minutes, single use).
        """
        device_id = str(uuid.uuid4())
        logger.info("Registering new device with reMarkable cloud")
        resp = self.session.post(
            DEVICE_TOKEN_URL,
            json={
                "code": one_time_code,
                "deviceDesc": DEVICE_DESC,
                "deviceID": device_id,
            },
        )
        if resp.status_code != 200 or not resp.text.strip():
            raise RegistrationError(
                f"Device registration failed ({resp.status_code}): {resp.text[:300]}"
            )

        self.credentials = Credentials(
            device_token=resp.text.strip(), device_id=device_id, user_token=None
        )
        self.credentials.save(self.credentials_path)
        logger.info("Device registered and credentials saved to %s", self.credentials_path)
        return self.credentials.device_token

    def get_user_token(self, force_refresh: bool = False) -> str:
        """Return a valid (non-expired) user token, refreshing it if needed."""
        if not self.credentials.device_token:
            raise NotRegisteredError(
                "No device token found. Call Auth.register(one_time_code) first, "
                "using a code from https://my.remarkable.com/device/browser/connect"
            )

        if not force_refresh and self.credentials.user_token:
            claims = _decode_jwt_payload(self.credentials.user_token)
            exp = claims.get("exp")
            if exp is None or time.time() < exp - _EXPIRY_SAFETY_MARGIN_SECONDS:
                return self.credentials.user_token

        logger.info("Refreshing user token")
        resp = self.session.post(
            USER_TOKEN_URL,
            headers={"Authorization": f"Bearer {self.credentials.device_token}"},
        )
        if resp.status_code != 200 or not resp.text.strip():
            raise TokenRefreshError(
                f"User token refresh failed ({resp.status_code}): {resp.text[:300]}"
            )

        self.credentials.user_token = resp.text.strip()
        self.credentials.save(self.credentials_path)
        return self.credentials.user_token

    def user_id_claim(self) -> Optional[str]:
        """Best-effort extraction of the account identifier embedded in the user token.

        Used only as an input to the sync-host discovery call; the reMarkable
        backend appears to accept a range of values here.
        """
        token = self.credentials.user_token
        if not token:
            return None
        claims = _decode_jwt_payload(token)
        return claims.get("sub") or claims.get("u") or claims.get("user_id")
