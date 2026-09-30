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

import asyncio
import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Self, cast, final

import aiohttp
from pydantic import BaseModel

from .exceptions import NotRegisteredError, RegistrationError, TokenRefreshError

log = logging.getLogger(__name__)

AUTH_BASE = "https://webapp-prod.cloud.remarkable.engineering"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/token/json/2/device/new"
USER_TOKEN_URL = f"{AUTH_BASE}/token/json/2/user/new"
DEVICE_DESC = "browser-chrome"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "remarkable" / "credentials.json"
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


class Claims(BaseModel):
    exp: int


class Credentials(BaseModel):
    device_token: str | None = None
    device_id: str | None = None
    user_token: str | None = None

    @classmethod
    def load(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        data = cast(dict[str, str], json.loads(path.read_text()))
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
        path.chmod(0o600)


def _decode_jwt_payload(token: str) -> Claims | None:
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
        return Claims.model_validate_json(payload)
    except Exception:
        return None


@final
class Auth:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    ):
        self.credentials_path: Path = Path(credentials_path)
        self.session = session
        self.credentials = Credentials.load(self.credentials_path)
        # Serializes refreshes so concurrent requests share a single new token.
        self._refresh_lock = asyncio.Lock()

    @property
    def is_registered(self) -> bool:
        return bool(self.credentials.device_token)

    async def register(self, one_time_code: str) -> str:
        device_id = str(uuid.uuid4())
        log.info("Registering new device %s", device_id)
        async with self.session.post(
            DEVICE_TOKEN_URL,
            headers={"Authorization": "Bearer"},
            json={
                "code": one_time_code,
                "deviceDesc": DEVICE_DESC,
                "deviceID": device_id,
            },
        ) as resp:
            text = (await resp.text()).strip()
            if resp.status != 200 or not text:
                raise RegistrationError(
                    f"Device registration failed ({resp.status}): {text[:300]}"
                )

        self.credentials = Credentials(device_token=text, device_id=device_id, user_token=None)
        self.credentials.save(self.credentials_path)
        log.info("Device registered and credentials saved to %s", self.credentials_path)
        return text

    def _cached_user_token(self) -> str | None:
        token = self.credentials.user_token
        if token and (claims := _decode_jwt_payload(token)):
            if time.time() < claims.exp - _EXPIRY_SAFETY_MARGIN_SECONDS:
                return token
        return None

    async def get_user_token(self, force: bool = False) -> str:
        if not self.credentials.device_token:
            raise NotRegisteredError(
                f"No device token in {self.credentials_path}. Run `remarkable register CODE` "
                "with a code from https://my.remarkable.com/device/browser/connect"
            )

        if not force and (token := self._cached_user_token()):
            return token

        stale = self.credentials.user_token
        async with self._refresh_lock:
            # Another task may have refreshed while we waited for the lock.
            if self.credentials.user_token != stale and (token := self._cached_user_token()):
                return token

            log.info("Refreshing user token")
            async with self.session.post(
                USER_TOKEN_URL,
                headers={"Authorization": f"Bearer {self.credentials.device_token}"},
            ) as resp:
                text = (await resp.text()).strip()
                if resp.status != 200 or not text:
                    raise TokenRefreshError(
                        f"User token refresh failed ({resp.status}): {text[:300]}"
                    )

            self.credentials.user_token = text
            self.credentials.save(self.credentials_path)
            return text
