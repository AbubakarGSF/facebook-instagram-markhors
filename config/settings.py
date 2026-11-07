"""Settings loader - Railway env vars se config load karta hai."""

import logging
import os
import sys
from typing import Set

logger = logging.getLogger(__name__)


def _env_list(name: str, default: str = "") -> Set[int]:
    raw = os.getenv(name, default).strip()
    return {int(x) for x in raw.split(",") if x.strip()}


BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN")
ADMIN_IDS = _env_list("ADMIN_IDS", "")
WEBSITE = os.getenv("WEBSITE", "https://socialmarkhors.me")
CREDIT = os.getenv("CREDIT", "Config by Social Markhors")

CONCURRENCY = int(os.getenv("CONCURRENCY", "8"))
TIMEOUT = float(os.getenv("TIMEOUT", "25"))
ACC_TIMEOUT = float(os.getenv("ACC_TIMEOUT", "90"))
RETRIES = int(os.getenv("RETRIES", "3"))
MAX_FILES = int(os.getenv("MAX_FILES", "15000"))
POST_MIN = float(os.getenv("POST_MIN", "45"))
POST_MAX = float(os.getenv("POST_MAX", "110"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
FB_TABLE = os.getenv("FB_TABLE", "fb_accounts")
IG_TABLE = os.getenv("IG_TABLE", "ig_accounts")

PROXIES = os.getenv("PROXIES", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "socks5").strip().lower()
PROXY_MODE = os.getenv("PROXY_MODE", "rotate").strip().lower()
PROXY_TEST_URL = os.getenv("PROXY_TEST_URL", "https://graph.facebook.com/me")

HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "0"))
HEALTH_CHECK_HOST = os.getenv("HEALTH_CHECK_HOST", "0.0.0.0")

NETCHECK_ON_START = os.getenv("NETCHECK_ON_START", "1").strip().lower() in ("1", "true", "yes", "on")

STATE_DIR = os.getenv("STATE_DIR", "./data")

# Zip bomb protection
ZIP_MAX_TOTAL = int(os.getenv("ZIP_MAX_TOTAL", str(256 * 1024 * 1024)))  # 256MB default
ZIP_MAX_FILE = int(os.getenv("ZIP_MAX_FILE", str(16 * 1024 * 1024)))  # 16MB per entry

# Bot version
BOT_VERSION = "2.2.0"

# Encryption key for cookie storage at rest
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
if not ENCRYPTION_KEY:
    logger.critical(
        "ENCRYPTION_KEY env var is REQUIRED but not set. "
        "Cookies cannot be encrypted at rest. Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )
    sys.exit(1)
