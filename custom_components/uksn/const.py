from __future__ import annotations

DOMAIN = "uksn"

CONF_PHONE = "phone"
CONF_PASSWORD = "password"
CONF_PROVIDER_ID = "provider_id"
CONF_BRAND_CODE = "brand_code"
CONF_SELECTED_ADDRESSES = "selected_addresses"

CONF_AUTH_TOKEN = "auth_token"
CONF_VITE_APP_X = "vite_app_x"

DEFAULT_BRAND_CODE = "ETALON"
DEFAULT_PROVIDER_ID = 18

UPDATE_INTERVAL_SECONDS = 60 * 60  # раз в час

BASE_URL = "https://cab.uksn.ru"
USER_AGENT = "HomeAssistant-UKSN/0.3"

SERVICE_REAUTH = "reauth"
SERVICE_FETCH_INVOICE_HISTORY = "fetch_invoice_history"

DEFAULT_HISTORY_MAX_MONTHS = 120
HISTORY_STOP_AFTER_ERRORS = 2

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_invoice_history"