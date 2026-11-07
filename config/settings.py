"""Settings loader - Railway env vars se config load karta hai."""

import logging
import os
import sys
from typing import Set

logger = logging.getLogger(__name__)


def _env_list(name: str, default: str = "") -> Set[int]:
    raw = os.getenv(name, default).strip()
    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Ignoring invalid id in %s: %r", name, part)
    return ids


BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN")
ADMIN_IDS = _env_list("ADMIN_IDS", "")
BRAND = os.getenv("BRAND", "Evil Markhors")
WEBSITE = os.getenv("WEBSITE", "https://evilmarkhors.me, https://evilmarkhors.tech")
CREDIT = os.getenv("CREDIT", "Config by Evil Markhors")

# Branding links (reworked cookie files + campaign post signature)
WHATSAPP_URL = os.getenv(
    "WHATSAPP_URL", "https://whatsapp.com/channel/0029Vb5qjwcG8l5CaqjMSU0K"
)
TELEGRAM_URL = os.getenv("TELEGRAM_URL", "https://t.me/+IGM63Xe1A-43ZTdk")
TWITTER_URL = os.getenv("TWITTER_URL", "https://x.com/EvilMarkhors")
FACEBOOK_URL = os.getenv(
    "FACEBOOK_URL", "https://www.facebook.com/share/19FyzDCNe6/"
)

# Campaign default hashtag + auto branding footer on every post
DEFAULT_TAG = os.getenv("DEFAULT_TAG", "#EvilMarkhors")
POST_BRANDING = os.getenv("POST_BRANDING", "1").strip().lower() in (
    "1", "true", "yes", "on"
)

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

# Railway sets PORT for public networking; use it unless HEALTH_CHECK_PORT overrides.
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", os.getenv("PORT", "0")))
HEALTH_CHECK_HOST = os.getenv("HEALTH_CHECK_HOST", "0.0.0.0")

NETCHECK_ON_START = os.getenv("NETCHECK_ON_START", "1").strip().lower() in ("1", "true", "yes", "on")

STATE_DIR = os.getenv("STATE_DIR", "./data")

# Zip bomb protection
ZIP_MAX_TOTAL = int(os.getenv("ZIP_MAX_TOTAL", str(256 * 1024 * 1024)))  # 256MB default
ZIP_MAX_FILE = int(os.getenv("ZIP_MAX_FILE", str(16 * 1024 * 1024)))  # 16MB per entry

# Proxy pe DEAD milne par direct (Railway IP) se confirm karna (1 = haan).
# Accuracy ke liye better, lekin Railway IP target site ko dik sakta hai.
VERIFY_CONFIRM_DEAD = os.getenv("VERIFY_CONFIRM_DEAD", "1").strip().lower() in (
    "1", "true", "yes", "on"
)

# Bot version
BOT_VERSION = "2.3.3"

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
