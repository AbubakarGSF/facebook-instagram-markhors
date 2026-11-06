"""Telegram bot handlers for Social Markhors Bot.

Admin commands: /start, /help, /status, /diag, /proxy, /netcheck, /rework,
/verify, /live, /camp, /stopcamp, /db, /db stats, /db export, /db reverify
"""

import asyncio
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
import zipfile as _zipfile
from typing import Dict, List, Optional

import filetype
import httpx

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, BOT_VERSION, CREDIT, CONCURRENCY, MAX_FILES, PROXIES, STATE_DIR, WEBSITE, ZIP_MAX_TOTAL
from src.database.supabase import SupabaseDB
from src.platforms.base import BasePlatform
from src.platforms.facebook import FacebookPlatform
from src.platforms.instagram import InstagramPlatform
from src.utils.crypto import decrypt_cookies, encrypt_cookies
from src.utils.proxy import load_proxies, proxy_status_text, norm_proxy, mask_sensitive
from src.utils.ratelimit import rate_limiter

logger = logging.getLogger(__name__)


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

    # Also clean up old campaign configs (not running campaigns)
    expired_camps = [
        uid for uid, cfg in CAMPS.items()
        if now - cfg.get("stage_at", now) > ttl and uid not in CAMPRUN
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


def new_camp() -> dict:
    return {
        "name": "",
        "platform": "",
        "tag": "#SocialMarkhors",
        "filter": {"countries": [], "years": [], "min_followers": 0},
        "limit": 0,
        "delay": [45, 110],
        "max_parts": 3,
        "cleanup_partial": True,
        "dry_run": False,
        "source": "zip",
        "stage": "idle",
        "stage_at": 0.0,
        "media": [],
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


def safe_text(text: str, max_len: int = 4096) -> str:
    """Sanitize text input - strip dangerous characters and limit length."""
    if not text:
        return ""
    # Remove control characters except newline/tab
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Limit length
    if len(text) > max_len:
        text = text[:max_len] + "..."
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


# ============================ BASIC HANDLERS ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        "Social Markhors Bot\n\n"
        "Platforms: Facebook, Instagram\n\n"
        "Modes:\n"
        "1) COOKIE REWORK - /rework > cookie .txt bhejo\n"
        "2) VERIFY - /verify > cookie zip bhejo\n"
        "3) CAMPAIGN - /camp new > /camp start\n\n"
        "Admin: /db /proxy /netcheck\n\n"
        "Full commands: /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        "REWORK: /rework > cookie .txt bhejo\n"
        "Supported formats:\n"
        "  X/Twitter:   [plus] [2fa false] [email].txt\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
        "  Reddit:      [1500 karma] [verified true] [username].txt\n"
        "  Generic:     [free] [email].txt, free data @email .txt\n\n"
        "VERIFY: /verify > cookie zip bhejo\n"
        "LIVE: /live on|off\n"
        "DB: /db stats | /db export fb | /db export ig\n"
        "PROXY: /proxy test | /proxy add [type] host:port:user:pass\n"
        "NETCHECK: /netcheck\n\n"
        "CAMPAIGN: /camp new > /camp text > /camp start\n\n"
        "Admin: /db /proxy /netcheck /camp"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    fb = "ON" if _fb_platform else "OFF"
    ig = "ON" if _ig_platform else "OFF"
    await update.message.reply_text(
        f"Social Markhors Bot\n"
        f"Credit: {CREDIT}\n"
        f"Web: {WEBSITE}\n"
        f"Admin: {uid in ADMIN_IDS}\n"
        f"Facebook: {fb}\n"
        f"Instagram: {ig}\n"
        f"DB: {'ON' if _db else 'OFF'}"
    )


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
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    if not check_user_rate_limit(update.effective_user.id):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(proxy_status_text())


async def netcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    if not check_user_rate_limit(update.effective_user.id):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        "Netcheck: Facebook + Instagram + Supabase reachability test\n"
        "Coming soon in next build."
    )


async def rework_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    user_state[uid] = {"mode": "rework", "platform": None, "at": time.time()}
    await update.message.reply_text(
        "Mode = rework. Ab cookie .txt bhejo.\n"
        "Supported formats:\n"
        "  X/Twitter:   [plus] [2fa false] [email].txt\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
        "  Reddit:      [1500 karma] [verified true] [username].txt\n"
        "  Generic:     [free] [email].txt, free data @email .txt\n\n"
        "Output: branded zip with standard format (platform metadata included)."
    )


async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    user_state[uid] = {"mode": "verify", "at": time.time()}
    await update.message.reply_text(
        "Mode = verify. Ab cookie zip bhejo.\n"
        "Supported formats:\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
    )


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
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


async def db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    if not check_user_rate_limit(update.effective_user.id):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    args = context.args
    sub = args[0].lower() if args else "stats"

    if not _db:
        await update.message.reply_text("SUPABASE_URL / KEY set nahi.")
        return

    async with httpx.AsyncClient() as cl:
        if sub == "stats":
            fb_count = await _db.count(cl, "fb_accounts")
            ig_count = await _db.count(cl, "ig_accounts")
            await update.message.reply_text(
                f"DB stats:\nfb_accounts: {fb_count}\nig_accounts: {ig_count}"
            )
        elif sub == "export":
            platform = args[1].lower() if len(args) > 1 else "fb"
            table = "fb_accounts" if platform == "fb" else "ig_accounts"
            await update.message.reply_text(f"Export from {table} - coming in next build")
        else:
            await update.message.reply_text("/db stats | /db export fb | /db export ig")


# ============================ CAMPAIGN ============================

async def camp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
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
            f"Source: {cfg['source']}\n"
            f"Filter: {cfg['filter']}\n"
            f"Limit: {cfg['limit']}\n"
            f"Delay: {cfg['delay'][0]}-{cfg['delay'][1]}s\n"
            f"Dry run: {cfg['dry_run']}\n"
            f"Texts: {len(cfg['texts'])}\n"
            f"Media: {len(cfg['media'])}"
        )
        return

    cfg = CAMPS.get(uid)
    if not cfg:
        await update.message.reply_text("Pehle /camp new <name>.")
        return

    if sub == "new":
        cfg = new_camp()
        cfg["name"] = safe_text((" ".join(args[1:]) or "camp"), 40)
        CAMPS[uid] = cfg
        save_camp(uid)
        await update.message.reply_text(
            f"Bana: {cfg['name']}\n"
            f"Ab /camp platform facebook|instagram karo."
        )
        return

    if sub == "platform":
        p = (args[1] if len(args) > 1 else "").lower()
        if p not in ("facebook", "instagram", "fb", "ig"):
            await update.message.reply_text("facebook|instagram")
            return
        cfg["platform"] = "facebook" if p in ("facebook", "fb") else "instagram"
        save_camp(uid)
        await update.message.reply_text(f"Platform = {cfg['platform']}")
        return

    if sub == "text":
        cfg["stage"] = "text"
        save_camp(uid)
        await update.message.reply_text(
            f"Text mode. Variants bhejo.\n/camp idle se band."
        )
        return

    if sub == "idle":
        cfg["stage"] = "idle"
        save_camp(uid)
        await update.message.reply_text("Band.")
        return

    if sub == "media":
        cfg["stage"] = "media"
        save_camp(uid)
        await update.message.reply_text("Photo/video bhejo.")
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
            f["countries"] = [x.upper() for x in " ".join(args[2:]).split(",") if x.strip()]
        elif k == "year":
            f["years"] = [x.strip() for x in " ".join(args[2:]).split(",") if x.strip()]
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
            await update.message.reply_text(f"Variant {i+1}: {safe_text(t, 4096)}")
        return

    if sub == "start":
        if uid in CAMPRUN:
            await update.message.reply_text("Pehle wala campaign chal raha hai. /stopcamp ya /cancel.")
            return
        if not cfg["texts"]:
            await update.message.reply_text("Pehle /camp text se variants bhejo.")
            return
        if cfg["source"] == "db" and not _db:
            await update.message.reply_text("DB off. /camp source zip")
            return

        cfg["stage"] = "cookies"
        cfg["stage_at"] = time.time()
        save_camp(uid)
        user_state[uid] = {"mode": "campaign", "platform": cfg["platform"], "at": time.time()}
        await update.message.reply_text(
            "Ab cookie zip bhejo (campaign ke liye)."
        )
        return

    if sub == "reset":
        CAMPS.pop(uid, None)
        await update.message.reply_text("Reset.")
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
    else:
        await update.message.reply_text("Koi campaign nahi.")


# ============================ CAMPAIGN LOOP ============================

async def _campaign_loop(uid: int, cfg: dict, accounts: list, state: dict):
    """Campaign loop: verify each account, post text, save to DB."""
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

    sem = asyncio.Semaphore(max(CONCURRENCY, 1))

    async def process_account(acc):
        nonlocal posted_count, failed_count
        if state.get("stop"):
            return

        ck = acc.get("cookies", {})
        if not ck:
            failed_count += 1
            return

        # Decrypt cookies if they were stored encrypted
        if _db and ck:
            ck = decrypt_cookies(ck)

        # Pick a proxy if available
        proxy = None
        if PROXIES:
            proxy = random.choice(PROXIES)

        cl = platform_obj.http_client(proxy=proxy)
        try:
            check = await platform_obj.live_check(cl, ck)
        finally:
            await cl.aclose()

        status = check.get("Status", "UNKNOWN")
        if status != "LIVE":
            failed_count += 1
            return

        # Check filters
        username = safe_text(check.get("username", ""))
        country = check.get("country", "")
        followers = int(check.get("Followers", 0) or 0)

        if filt.get("countries") and country not in filt["countries"]:
            return
        if filt.get("min_followers", 0) > followers:
            return

        # Post
        text = random.choice(texts)
        ok, post_id, err = await platform_obj.post(cl, ck, safe_text(text))

        if ok:
            posted_count += 1
            # Save to DB
            if _db:
                row = {
                    "username": username,
                    "fb_id": check.get("fb_id", "") if platform == "facebook" else "",
                    "ig_id": check.get("ig_id", "") if platform == "instagram" else "",
                    "cookies": encrypt_cookies(ck),
                    "country": country,
                    "followers": followers,
                    "verified": str(check.get("Verified", "false")).lower() == "true",
                    "status": "live",
                    "source": "campaign",
                    "last_post": time.time(),
                }
                table = "fb_accounts" if platform == "facebook" else "ig_accounts"
                async with httpx.AsyncClient() as db_cl:
                    await _db.upsert(db_cl, table, [row])
        else:
            failed_count += 1

        # Delay between accounts
        lo, hi = cfg["delay"]
        await asyncio.sleep(random.uniform(lo, hi))

    # Process accounts with concurrency limit
    tasks = [process_account(acc) for acc in accounts]
    await asyncio.gather(*tasks, return_exceptions=True)

    await update.message.reply_text(
        f"Campaign complete!\n"
        f"Posted: {posted_count}\n"
        f"Failed: {failed_count}\n"
        f"Total: {prog_total}"
    )
    # Clear campaign mode
    user_state.pop(uid, None)


# ============================ FILE HANDLING ============================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookie file uploads for rework/verify/campaign."""
    uid = update.effective_user.id
    doc = update.message.document
    if not doc:
        return

    state = user_state.get(uid, {})
    mode = state.get("mode", "rework")

    if not doc.file_name or not doc.file_name.lower().endswith((".txt", ".zip")):
        await update.message.reply_text("Sirf .txt ya .zip file bhejo.")
        return

    msg = await update.message.reply_text("File process ho rahi hai...")

    # Clean up expired user state entries
    cleanup_user_state()

    # Check if proxies are configured - without them all traffic goes direct
    if not PROXIES:
        await msg.edit_text(
            "⚠️ No proxies configured! All X traffic will go direct.\n"
            "Add proxies with /proxy add before uploading cookies."
        )

    tmp = tempfile.mkdtemp(prefix="sm_")
    try:
        file = await context.bot.get_file(doc.file_id)
        path = os.path.join(tmp, doc.file_name)
        await file.download_to_drive(path)

        # MIME type validation using filetype (pure Python, no libmagic needed)
        kind = filetype.guess(path)
        if kind is None:
            await msg.edit_text("Invalid file type. Only .txt and .zip files are allowed.")
            return
        mime = kind.mime
        if mime not in ("application/zip", "text/plain"):
            await msg.edit_text("Invalid file type. Only .txt and .zip files are allowed.")
            return

        results = []

        if doc.file_name.lower().endswith(".zip"):
            with _zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                # Zip bomb protection: check total uncompressed size
                total_uncompressed = sum(z.getinfo(n).file_size for n in names)
                if total_uncompressed > ZIP_MAX_TOTAL:
                    await msg.edit_text(
                        f"Zip bomb detected: total uncompressed size {total_uncompressed // (1024*1024)}MB "
                        f"exceeds limit {ZIP_MAX_TOTAL // (1024*1024)}MB"
                    )
                    return
                for name in names[:MAX_FILES]:
                    raw = z.read(name).decode("utf-8", "ignore")
                    result = await _process_one(name, raw, state)
                    results.append(result)
        else:
            with open(path, encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            result = await _process_one(doc.file_name, raw, state)
            results.append(result)

        # If campaign mode, run campaign loop
        if mode == "campaign" and state.get("platform"):
            cfg = CAMPS.get(uid)
            if cfg and cfg.get("texts"):
                await update.message.reply_text(
                    f"Campaign shuru: {len(results)} accounts\nPlatform: {cfg['platform']}"
                )
                await _campaign_loop(uid, cfg, results, {})
            else:
                await update.message.reply_text("Campaign configs missing. /camp text bhejo pehle.")
        else:
            # Create output zip for rework/verify mode
            out_path = os.path.join(tmp, "out.zip")
            with _zipfile.ZipFile(out_path, "w", _zipfile.ZIP_DEFLATED) as zout:
                for name, output, status in results:
                    folder = "working" if status == "LIVE" else ("dead" if status == "DEAD" else "unknown")
                    # Path traversal protection: use basename only
                    safe_name = safe_zip_entry_name(name)
                    zout.writestr(f"{folder}/{safe_name}", output)

            await context.bot.send_document(
                update.effective_chat.id,
                document=open(out_path, "rb"),
                filename="reworked.zip",
                caption=f"Rework complete | {len(results)} files",
            )
            await msg.edit_text("Complete")

    except Exception as e:
        logger.exception("handle_file")
        await msg.edit_text("File process karne mein error aaya. Dobara try karein.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _process_one(name: str, raw: str, state: dict) -> tuple:
    """Process a single cookie file."""
    name_lower = name.lower()
    if "instagram" in name_lower or "ig" in name_lower:
        platform = "instagram"
    elif "facebook" in name_lower or "fb" in name_lower:
        platform = "facebook"
    else:
        platform = state.get("platform", "instagram")

    platform_obj = get_platform(platform)
    bp = BasePlatform()
    meta, cookies = bp.parse_cookie_file(raw)

    if not cookies:
        return (name, "", "no cookies")

    # Live check if enabled
    if state.get("live", False) and platform_obj:
        cl = platform_obj.http_client()
        try:
            check = await platform_obj.live_check(cl, cookies)
            status = check.get("Status", "UNKNOWN")
            reason = check.get("Reason", "")
            meta.update(check)
        finally:
            await cl.aclose()
    else:
        status = "UNKNOWN"
        reason = "live check off"

    meta.update({"Status": status, "Reason": reason})
    output = bp.build_cookie_file(meta, cookies)

    # Save LIVE accounts to DB
    if status == "LIVE" and _db:
        table = "fb_accounts" if platform == "facebook" else "ig_accounts"
        encrypted_cookies = encrypt_cookies(cookies)
        row = {
            "username": meta.get("username", ""),
            "fb_id": meta.get("fb_id", "") if platform == "facebook" else "",
            "ig_id": meta.get("ig_id", "") if platform == "instagram" else "",
            "cookies": encrypted_cookies,
            "country": meta.get("country", ""),
            "followers": int(meta.get("Followers", 0) or 0),
            "verified": str(meta.get("Verified", "false")).lower() == "true",
            "status": "live",
            "source": "bot",
        }
        async with httpx.AsyncClient() as cl:
            await _db.upsert(cl, table, [row])

    return (name, output, status)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    user_state.pop(uid, None)
    await update.message.reply_text("Cancel / reset.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages for campaign variant collection."""
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return

    state = user_state.get(uid, {})
    if not state or state.get("mode") != "campaign" or state.get("stage") != "text":
        return

    cfg = CAMPS.get(uid)
    if not cfg:
        await update.message.reply_text("Campaign not found. Use /camp new to create one.")
        return

    text = safe_text(update.message.text or "", max_len=4096)
    if not text:
        await update.message.reply_text("Empty text not allowed.")
        return

    cfg["texts"].append(text)
    save_camp(uid)
    await update.message.reply_text(f"Variant {len(cfg['texts'])} added: {safe_text(text, 80)}...")