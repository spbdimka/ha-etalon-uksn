from __future__ import annotations

import asyncio
import json as jsonlib
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import BASE_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)


class UKSNAuthError(Exception):
    """Ошибка авторизации/сессии."""


class UKSNRequestError(Exception):
    """Ошибка запроса к API."""


def _truncate(s: str, n: int = 1200) -> str:
    if s is None:
        return ""
    return s if len(s) <= n else s[:n] + "…(truncated)"


def _redact_payload(payload: Any) -> Any:
    """Не логируем пароль."""
    if isinstance(payload, dict):
        out = dict(payload)
        if "password" in out:
            out["password"] = "***REDACTED***"
        return out
    if isinstance(payload, list):
        # массив показаний: там нет пароля, можно не трогать
        return payload
    return payload


@dataclass
class UKSNClient:
    session: aiohttp.ClientSession
    base_url: str = BASE_URL

    # Если выяснится, что API требует токен в заголовке — положим сюда
    auth_token: str | None = None
    temp_token: str | None = None

    def dump_cookies(self) -> list[dict[str, Any]]:
        """Показать cookie-jar (без значений можно потом замаскировать)."""
        items: list[dict[str, Any]] = []
        for c in self.session.cookie_jar:
            items.append(
                {
                    "name": c.key,
                    "domain": getattr(c, "domain", None),
                    "path": getattr(c, "path", None),
                    # значение в логах обычно не надо; оставил для отладки — можешь убрать
                    "value": c.value,
                }
            )
        return items

    async def _request(
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

        # если позже выясним, что нужен токен — он пойдёт сюда
        if self.auth_token:
            hdrs["Authorization"] = f"Bearer {self.auth_token}"

        # если temp_token надо передавать — будет видно, куда именно его вставить
        if self.temp_token:
            hdrs["X-Temp-Token"] = self.temp_token

        if headers:
            hdrs.update(headers)

        last_exc: Exception | None = None

        for attempt in range(1, 4):
            try:
                _LOGGER.debug(
                    "HTTP %s %s params=%s json=%s",
                    method,
                    url,
                    params,
                    _redact_payload(json),
                )

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

                    _LOGGER.debug("HTTP %s %s -> %s (%s)", method, url, resp.status, ctype)

                    # читаем тело 1 раз ВСЕГДА (так мы увидим и 401 body)
                    raw = await resp.read()

                    body_text: str | None = None
                    if raw:
                        try:
                            body_text = raw.decode("utf-8", errors="replace")
                        except Exception:
                            body_text = None

                    # ошибки: логируем тело
                    if resp.status >= 400:
                        if body_text is not None:
                            _LOGGER.debug("HTTP error body (first 1200 chars): %s", _truncate(body_text, 1200))
                        else:
                            _LOGGER.debug("HTTP error body: <binary %d bytes>", len(raw))

                        if resp.status in (401, 403):
                            raise UKSNAuthError(f"{resp.status}: {_truncate(body_text or '', 200)}")
                        raise UKSNRequestError(f"{resp.status}: {_truncate(body_text or '', 200)}")

                    # успешный ответ
                    if "application/json" in ctype:
                        try:
                            data = jsonlib.loads(body_text or "null")
                        except Exception:
                            _LOGGER.debug("JSON parse failed, returning raw text")
                            return body_text or raw

                        # в debug режиме полезно видеть ответ (без пароля там обычно нет)
                        _LOGGER.debug("HTTP JSON response (first 1200 chars): %s", _truncate(jsonlib.dumps(data, ensure_ascii=False), 1200))
                        return data

                    # текст/прочее
                    if body_text is not None:
                        _LOGGER.debug("HTTP text response (first 1200 chars): %s", _truncate(body_text, 1200))
                        return body_text

                    return raw

            except (aiohttp.ClientError, asyncio.TimeoutError, UKSNRequestError, UKSNAuthError) as e:
                last_exc = e
                if attempt < 3:
                    await asyncio.sleep(0.6 * attempt)
                    continue
                raise

        raise UKSNRequestError(f"Request failed: {last_exc}")

    async def get_temp_token(self) -> Any:
        return await self._request("GET", "/api/m/getTempToken")

    async def auth_login(
        self,
        phone: str,
        password: str,
        provider_id: str,
        device_id: str,
        brand_code: str,
    ) -> Any:
        payload = {
            "phone": phone,
            "password": password,
            "provider_id": provider_id,
            "brand_code": brand_code,
            "path": "/",
            "i": device_id,
        }
        return await self._request("POST", "/web_api/auth/login", json=payload)

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