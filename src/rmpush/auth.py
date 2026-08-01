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
from typing import Self, cast, final
from pydantic import BaseModel

import requests

from .exceptions import NotRegisteredError, RegistrationError, TokenRefreshError

log = logging.getLogger(__name__)

AUTH_BASE = "https://webapp-prod.cloud.remarkable.engineering"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/token/json/2/device/new"
USER_TOKEN_URL = f"{AUTH_BASE}/token/json/2/user/new"
DEVICE_DESC = "browser-chrome"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "rmpush" / "credentials.json"
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
        credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
        session: requests.Session | None = None,
    ):
        self.credentials_path: Path = Path(credentials_path)
        self.session = session or requests.Session()
        self.credentials = Credentials.load(self.credentials_path)

    @property
    def is_registered(self) -> bool:
        return bool(self.credentials.device_token)

    def register(self, one_time_code: str) -> str:
        device_id = str(uuid.uuid4())
        log.info("Registering new device with")
        resp = self.session.post(
            DEVICE_TOKEN_URL,
            headers={"Authorization": "Bearer"},
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
        log.info("Device registered and credentials saved to %s", self.credentials_path)
        return cast(str, self.credentials.device_token)

    def get_user_token(self, force: bool = False) -> str:
        if not self.credentials.device_token:
            raise NotRegisteredError()

        if not force and self.credentials.user_token:
            if claims := _decode_jwt_payload(self.credentials.user_token):
                if time.time() < claims.exp - _EXPIRY_SAFETY_MARGIN_SECONDS:
                    return self.credentials.user_token

        log.info("Refreshing user token")
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
