import asyncio
import base64
import json
import time

from conftest import FakeSession
from remarkable.auth import USER_TOKEN_URL, Auth, Credentials


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


async def test_concurrent_callers_share_a_single_refresh(session: FakeSession, tmp_path):
    creds_path = tmp_path / "credentials.json"
    Credentials(device_token="devicetoken", device_id="dev").save(creds_path)
    new_token = _jwt(time.time() + 3600)
    session.add("POST", USER_TOKEN_URL, body=new_token)

    auth = Auth(session, credentials_path=creds_path)  # type: ignore[arg-type]
    tokens = await asyncio.gather(*(auth.get_user_token() for _ in range(5)))

    assert tokens == [new_token] * 5
    assert len(session.requests) == 1
    assert session.requests[0].headers["Authorization"] == "Bearer devicetoken"
