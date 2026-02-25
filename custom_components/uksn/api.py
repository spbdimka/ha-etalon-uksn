from __future__ import annotations

import asyncio
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


@dataclass
class UKSNClient:
    session: aiohttp.ClientSession
    base_url: str = BASE_URL

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
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            hdrs.update(headers)

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=hdrs,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status in (401, 403):
                        raise UKSNAuthError(f"Auth failed: {resp.status}")

                    ctype = resp.headers.get("Content-Type", "")
                    if "application/json" in ctype:
                        return await resp.json()

                    # иногда API может вернуть текст/HTML на ошибках
                    text = await resp.text()
                    if resp.status >= 400:
                        raise UKSNRequestError(f"{resp.status}: {text[:300]}")
                    return text
            except (aiohttp.ClientError, asyncio.TimeoutError, UKSNRequestError, UKSNAuthError) as e:
                last_exc = e
                if attempt < 3:
                    await asyncio.sleep(0.6 * attempt)
                    continue
                raise

        raise UKSNRequestError(f"Request failed: {last_exc}")

    async def get_temp_token(self) -> Any:
        return await self._request("GET", "/api/m/getTempToken")

    async def auth_phone(self, phone: str, provider_id: str, device_id: str) -> Any:
        # тело ровно как у тебя в HAR: phone/provider_id/i
        payload = {"phone": phone, "provider_id": provider_id, "i": device_id}
        return await self._request("POST", "/web_api/auth/phone", json=payload)

    async def auth_confirm(self, phone: str, code: str, brand_code: str) -> Any:
        payload = {
            "phone": phone,
            "password": code,
            "brand_code": brand_code,
            "path": "/",
        }
        return await self._request("POST", "/web_api/auth/confirm", json=payload)

    async def get_addresses(self, domain_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/m/account/address", params={"domain_id": str(domain_id)})
        # ожидаем список, но оставим мягко
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