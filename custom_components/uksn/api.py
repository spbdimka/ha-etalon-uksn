from __future__ import annotations

import asyncio
import hashlib
import json as jsonlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from .const import BASE_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)

# Дамп HTTP (можешь включить на время)
DUMP_HTTP = False
DUMP_BODY_LIMIT = 2000

_AUTH_TOKEN_RE = re.compile(r"auth_Token=([^;]+)")
RE_VITE_APP_X = re.compile(r"""VITE_APP_X["']?\s*:\s*["'](?P<x>[^"']+)["']""")

# ВАЖНО: entry js у тебя живёт в ./static/index-XXXX.js
RE_ENTRY_JS = re.compile(
    r"""<script[^>]+src=["'](?P<src>[^"']*(?:\./)?static/index-[^"']+\.js)["']""",
    re.IGNORECASE,
)


class UKSNAuthError(Exception):
    """Unauthorized / Auth error."""


class UKSNRequestError(Exception):
    """HTTP / API error."""


def _truncate(s: str, n: int = 1200) -> str:
    if s is None:
        return ""
    return s if len(s) <= n else s[:n] + "…(truncated)"


def _redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        out = dict(payload)
        if "password" in out:
            out["password"] = "***REDACTED***"
        return out
    return payload


def _extract_auth_token(set_cookie_headers: list[str]) -> str | None:
    for h in set_cookie_headers:
        m = _AUTH_TOKEN_RE.search(h)
        if m:
            return m.group(1)
    return None


def _phone10(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:]


def _normalize_js_path(src: str) -> str:
    """
    Нормализация путей:
      ./static/index-xxx.js -> /static/index-xxx.js
      static/index-xxx.js   -> /static/index-xxx.js
      /static/index-xxx.js  -> /static/index-xxx.js
    """
    s = (src or "").strip()
    # часто бывает './static/...'
    if s.startswith("./"):
        s = s[1:]  # './static/..' -> '/static/..'
    if not s.startswith("/"):
        s = "/" + s
    return s


@dataclass
class UKSNClient:
    session: aiohttp.ClientSession
    base_url: str = BASE_URL

    phone: str | None = None
    password: str | None = None
    brand_code: str | None = None

    auth_token: str | None = None
    vite_app_x: str | None = None
    vite_app_x_fetched_at: datetime | None = None

    async def fetch_vite_app_x(self, force: bool = False) -> str | None:
        # кэш на сутки
        if not force and self.vite_app_x and self.vite_app_x_fetched_at:
            if datetime.utcnow() - self.vite_app_x_fetched_at < timedelta(days=1):
                return self.vite_app_x

        # чаще всего SPA index находится на "/"
        html = await self._request_once("GET", "/", headers={"Accept": "text/html, */*"})
        if not isinstance(html, str):
            try:
                html = html.decode("utf-8", errors="replace")
            except Exception:
                _LOGGER.debug("fetch_vite_app_x: cannot decode html")
                return self.vite_app_x

        # 1) иногда VITE_APP_X может быть прямо в HTML
        m0 = RE_VITE_APP_X.search(html)
        if m0:
            self.vite_app_x = m0.group("x")
            self.vite_app_x_fetched_at = datetime.utcnow()
            _LOGGER.info("Fetched VITE_APP_X from HTML (len=%d)", len(self.vite_app_x))
            return self.vite_app_x

        # 2) ищем entry js: <script type="module" ... src="./static/index-....js">
        m = RE_ENTRY_JS.search(html)
        if not m:
            _LOGGER.debug("fetch_vite_app_x: entry js not found in HTML head=%s", _truncate(html, 800))
            return self.vite_app_x

        js_path = _normalize_js_path(m.group("src"))
        js = await self._request_once("GET", js_path, headers={"Accept": "application/javascript, */*"})
        if not isinstance(js, str):
            try:
                js = js.decode("utf-8", errors="replace")
            except Exception:
                _LOGGER.debug("fetch_vite_app_x: cannot decode js %s", js_path)
                return self.vite_app_x

        m2 = RE_VITE_APP_X.search(js)
        if not m2:
            _LOGGER.debug("fetch_vite_app_x: VITE_APP_X not found in %s", js_path)
            return self.vite_app_x

        self.vite_app_x = m2.group("x")
        self.vite_app_x_fetched_at = datetime.utcnow()
        _LOGGER.info("Fetched VITE_APP_X len=%d from %s", len(self.vite_app_x), js_path)
        return self.vite_app_x

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any | None = None) -> Any:
        # wrapper: auto reauth once
        try:
            return await self._request_once(method, path, params=params, json=json)
        except UKSNAuthError as e:
            if self.phone and self.password and self.brand_code:
                _LOGGER.warning("Unauthorized on %s %s: %s. Trying re-auth...", method, path, e)
                await self.auth_login(self.phone, self.password, self.brand_code, force_vite_refresh=False)
                return await self._request_once(method, path, params=params, json=json)
            raise

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> Any:
        url = f"{self.base_url}{path}"

        hdrs: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

        if self.auth_token:
            hdrs["Cookie"] = f"auth_Token={self.auth_token}"

        if headers:
            hdrs.update(headers)

        if DUMP_HTTP:
            safe_json = _redact_payload(json)
            _LOGGER.debug(">>> %s %s", method, url)
            if params:
                _LOGGER.debug(">>> params: %s", params)
            if safe_json is not None:
                _LOGGER.debug(">>> json: %s", safe_json)

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                _LOGGER.debug("HTTP %s %s params=%s json=%s", method, url, params, _redact_payload(json))

                async with self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=hdrs,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    set_cookie = resp.headers.getall("Set-Cookie", [])
                    if set_cookie:
                        _LOGGER.debug("Set-Cookie (%s %s): %s", method, url, set_cookie)
                        tok = _extract_auth_token(set_cookie)
                        if tok:
                            self.auth_token = tok
                            _LOGGER.debug("Captured auth_Token=%s", tok.split("_", 1)[0] + "_***")

                    _LOGGER.debug("HTTP %s %s -> %s (%s)", method, url, resp.status, ctype)

                    raw = await resp.read()
                    body_text = raw.decode("utf-8", errors="replace") if raw else ""

                    if DUMP_HTTP:
                        _LOGGER.debug("<<< %s %s", resp.status, url)
                        _LOGGER.debug("<<< content-type: %s", ctype)
                        if set_cookie:
                            _LOGGER.debug("<<< set-cookie: %s", set_cookie)
                        _LOGGER.debug("<<< body: %s", _truncate(body_text, DUMP_BODY_LIMIT))

                    if resp.status >= 400:
                        _LOGGER.debug("HTTP error body (first 1200 chars): %s", _truncate(body_text, 1200))
                        if resp.status in (401, 403):
                            raise UKSNAuthError(f"{resp.status}: {_truncate(body_text, 200)}")
                        raise UKSNRequestError(f"{resp.status}: {_truncate(body_text, 200)}")

                    if "application/json" in ctype:
                        try:
                            data = jsonlib.loads(body_text or "null")
                        except Exception:
                            return body_text
                        _LOGGER.debug("HTTP JSON response (first 1200 chars): %s", _truncate(jsonlib.dumps(data, ensure_ascii=False), 1200))
                        return data

                    return body_text
            except (aiohttp.ClientError, asyncio.TimeoutError, UKSNRequestError, UKSNAuthError) as e:
                last_exc = e
                if attempt < 3:
                    await asyncio.sleep(0.6 * attempt)
                    continue
                raise

        raise UKSNRequestError(f"Request failed: {last_exc}")

    async def get_temp_token(self) -> dict[str, Any]:
        data = await self._request_once("GET", "/api/m/getTempToken")
        if not isinstance(data, dict):
            raise UKSNRequestError(f"getTempToken: unexpected response {data}")
        return data

    async def auth_login(self, phone: str, password: str, brand_code: str, path: str = "/", force_vite_refresh: bool = False) -> Any:
        x = await self.fetch_vite_app_x(force=force_vite_refresh)
        if not x:
            raise UKSNRequestError("VITE_APP_X not found (cannot calculate i)")

        tt = await self.get_temp_token()
        temp_token = tt.get("token")
        if not temp_token:
            raise UKSNRequestError(f"getTempToken: no token: {tt}")

        p10 = _phone10(phone)
        i_val = hashlib.md5((p10 + str(temp_token) + x).encode("utf-8")).hexdigest()

        payload = {
            "phone": p10,
            "password": password,
            "brand_code": brand_code,
            "path": path,
            "i": i_val,
        }

        resp = await self._request_once("POST", "/web_api/auth/login", json=payload)
        _LOGGER.info(
            "Login result ok=%s errorCode=%s auth_token=%s vite_x=%s",
            isinstance(resp, dict) and resp.get("ok"),
            isinstance(resp, dict) and resp.get("errorCode"),
            "present" if self.auth_token else "missing",
            "present" if self.vite_app_x else "missing",
        )

        if isinstance(resp, dict) and resp.get("ok") is True and not self.auth_token and not force_vite_refresh:
            _LOGGER.warning("Login ok but auth_token missing. Force VITE refresh and retry once.")
            return await self.auth_login(phone, password, brand_code, path=path, force_vite_refresh=True)

        return resp

    async def get_addresses(self, domain_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/m/account/address", params={"domain_id": str(domain_id)})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        raise UKSNRequestError("Unexpected addresses response shape")

    async def get_counters(self, address_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/api/m/v2/gku/counter/{address_id}")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        raise UKSNRequestError("Unexpected counters response shape")

    async def get_bill_detail(self, address_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/web_api/gku/v2/bill/{address_id}/detail")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        raise UKSNRequestError("Unexpected bill detail response shape")

    async def set_counter_value(self, counter_id: str, value: str) -> Any:
        payload = [{"counter_id": str(counter_id), "current_val": str(value)}]
        return await self._request("POST", "/api/m/v2/gku/counter", json=payload)