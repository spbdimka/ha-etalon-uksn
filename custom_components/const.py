from __future__ import annotations

DOMAIN = "uksn"

CONF_PHONE = "phone"
CONF_PROVIDER_ID = "provider_id"
CONF_DOMAIN_ID = "domain_id"
CONF_BRAND_CODE = "brand_code"
CONF_DEVICE_ID = "device_id"
CONF_SELECTED_ADDRESSES = "selected_addresses"

DEFAULT_BRAND_CODE = "ETALON"
DEFAULT_DOMAIN_ID = 25

# обновление раз в час
UPDATE_INTERVAL_SECONDS = 3600

BASE_URL = "https://cab.uksn.ru"
USER_AGENT = "HomeAssistant-UKSN/0.1"