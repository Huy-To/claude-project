"""Signed requests to OKX's private REST API (v5), for the Read and Trade permissions.

Credentials come from the environment (or .env in the project folder):

    OKX_API_KEY=...
    OKX_API_SECRET=...
    OKX_API_PASSPHRASE=...      # the passphrase you chose when creating the key
    OKX_DEMO=1                  # optional: send everything to OKX demo trading instead

Every request is signed as base64(HMAC-SHA256(secret, timestamp + METHOD + path + body)).
OKX rejects requests whose timestamp is more than 30 s off its own clock, so the client
measures the offset against /api/v5/public/time and corrects for it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("btcliq")
OKX = "https://www.okx.com"

# OKX error codes worth translating into something actionable.
HINTS = {
    "50101": "This API key belongs to the other environment (live vs demo). Check OKX_DEMO in .env.",
    "50102": "Your computer's clock is too far off OKX's. Sync the system clock.",
    "50103": "OKX_API_KEY is missing from the request.",
    "50104": "OKX_API_PASSPHRASE is missing.",
    "50105": "OKX_API_PASSPHRASE is wrong. It's the passphrase you set when creating the key.",
    "50110": "Your IP address isn't on this API key's IP allowlist. Add it in OKX → API.",
    "50111": "OKX_API_KEY isn't valid. Check it in .env.",
    "50113": "The signature was rejected: OKX_API_SECRET is probably wrong.",
    "50114": "OKX says the request authorization is invalid. Re-check all three credentials.",
    "50119": "This API key doesn't exist (deleted or mistyped).",
    "50120": "This API key lacks the permission for that action (Read or Trade).",
    "51000": "OKX rejected one of the order parameters.",
    "51008": "Not enough available balance or margin for this order.",
    "51010": "Your account mode doesn't allow this. Spot mode can't trade derivatives; switch to Futures mode in OKX.",
    "51121": "Order size must be a whole multiple of the lot size.",
    "51137": "Your price is above the highest price OKX allows right now.",
    "51138": "Your price is below the lowest price OKX allows right now.",
}


class OKXError(Exception):
    def __init__(self, code: str, msg: str, data=None):
        self.code, self.msg, self.data = str(code), msg or "", data
        hint = HINTS.get(self.code)
        super().__init__(f"OKX {self.code}: {self.msg}" + (f" ({hint})" if hint and hint not in self.msg else ""))


class OKXNotConfigured(Exception):
    pass


def sign(secret: str, ts: str, method: str, path: str, body: str = "") -> str:
    mac = hmac.new(secret.encode(), (ts + method.upper() + path + body).encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def iso_ts(epoch: float) -> str:
    """OKX wants ISO 8601 with milliseconds and a Z, e.g. 2026-11-13T09:08:57.715Z."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(epoch * 1000) % 1000:03d}Z"


class OKXClient:
    def __init__(self, session: aiohttp.ClientSession, key: str = "", secret: str = "", passphrase: str = "",
                 demo: bool = False, base: str = OKX):
        self.s, self.base = session, base
        self.key, self.secret, self.passphrase, self.demo = key, secret, passphrase, demo
        self.offset = 0.0           # OKX clock minus ours, in seconds
        self._synced = 0.0

    @classmethod
    def from_env(cls, session: aiohttp.ClientSession) -> "OKXClient":
        demo = os.environ.get("OKX_DEMO", "").strip().lower() in ("1", "true", "yes")
        return cls(session, os.environ.get("OKX_API_KEY", "").strip(), os.environ.get("OKX_API_SECRET", "").strip(),
                   os.environ.get("OKX_API_PASSPHRASE", "").strip(), demo)

    @property
    def configured(self) -> bool:
        return bool(self.key and self.secret and self.passphrase)

    @property
    def env_label(self) -> str:
        return "demo" if self.demo else "live"

    async def sync_clock(self, force: bool = False) -> None:
        if not force and time.time() - self._synced < 600:
            return
        try:
            t0 = time.time()
            async with self.s.get(self.base + "/api/v5/public/time", timeout=aiohttp.ClientTimeout(total=10)) as r:
                d = await r.json(content_type=None)
            t1 = time.time()
            self.offset = int(d["data"][0]["ts"]) / 1000 - (t0 + t1) / 2
            self._synced = t1
            if abs(self.offset) > 5:
                log.warning("Your clock is %.1f s off OKX's; correcting for it", self.offset)
        except Exception as e:  # signing still works if the clock is close enough
            log.warning("Couldn't read OKX server time: %s", e)

    def _headers(self, method: str, path: str, body: str) -> dict:
        ts = iso_ts(time.time() + self.offset)
        h = {"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": sign(self.secret, ts, method, path, body),
             "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase,
             "Content-Type": "application/json"}
        if self.demo:
            h["x-simulated-trading"] = "1"
        return h

    async def request(self, method: str, path: str, params: dict | None = None, body=None,
                      private: bool = True, timeout: float = 10, _retry: bool = True) -> list:
        """Returns OKX's `data` array. Raises OKXError when OKX says no."""
        method = method.upper()
        q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        full = path + ("?" + urlencode(q) if q else "")
        text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if private:
            if not self.configured:
                raise OKXNotConfigured("Add OKX_API_KEY, OKX_API_SECRET and OKX_API_PASSPHRASE to .env, then restart.")
            await self.sync_clock()
            headers = self._headers(method, full, text)
        elif self.demo:
            headers["x-simulated-trading"] = "1"
        async with self.s.request(method, self.base + full, data=text or None, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            try:
                d = await r.json(content_type=None)
            except Exception:
                raise OKXError(str(r.status), f"HTTP {r.status} from OKX: {(await r.text())[:200]}")
        code = str(d.get("code", ""))
        if code == "50102" and private and _retry:   # clock drifted: resync once and retry
            await self.sync_clock(force=True)
            return await self.request(method, path, params, body, private, timeout, _retry=False)
        if code != "0":
            data = d.get("data") or []
            # Per-item errors (orders, cancels) carry the useful message in sCode/sMsg.
            item = next((x for x in data if isinstance(x, dict) and str(x.get("sCode", "0")) != "0"), None)
            if item:
                raise OKXError(item["sCode"], item.get("sMsg", ""), data)
            raise OKXError(code, d.get("msg", ""), data)
        return d.get("data") or []

    # Thin wrappers, named after OKX's endpoints.
    def get(self, path, **params):
        return self.request("GET", path, params)

    def post(self, path, body):
        return self.request("POST", path, body=body)

    def public(self, path, **params):
        return self.request("GET", path, params, private=False)
