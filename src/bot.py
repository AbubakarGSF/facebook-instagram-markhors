"""Telegram bot handlers for Social Markhors Bot.

Admin commands: /start, /help, /status, /diag, /proxy, /netcheck, /rework,
/verify, /live, /camp, /db, /db stats, /db export, /db reverify
"""

import asyncio
import json
import logging
import os
import tempfile
import zipfile as _zipfile
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, CREDIT, STATE_DIR, WEBSITE, MAX_FILES
from src.database.supabase import SupabaseDB
from src.platforms.base import BasePlatform
from src.platforms.facebook import FacebookPlatform
from src.platforms.instagram import InstagramPlatform
from src.utils.proxy import load_proxies, proxy_status_text, norm_proxy

logger = logging.getLogger(__name__)


# Global state
_db: Optional[SupabaseDB] = None
_fb_platform: Optional[FacebookPlatform] = None
_ig_platform: Optional[InstagramPlatform] = None
user_state: Dict[int, dict] = {}
CAMPS: Dict[int, dict] = {}  # uid -> campaign config
CAMPRUN: Dict[int, dict] = {}  # uid -> running campaign state


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
        "platform": "",  # facebook or instagram
        "tag": "#SocialMarkhors",
        "roles": {},  # not used for FB/IG, but kept for compatibility
        "filter": {"countries": [], "years": [], "min_followers": 0},
        "limit": 0,
        "delay": [45, 110],
        "max_parts": 3,
        "cleanup_partial": True,
        "dry_run": False,
        "source": "zip",  # zip or db
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
        import json
        import os
        from config.settings import STATE_DIR
        os.makedirs(STATE_DIR, exist_ok=True)
        path = os.path.join(STATE_DIR, f"camp_{uid}.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except Exception:
        logger.exception("save_camp")


def load_camps():
    import json
    import os
    from config.settings import STATE_DIR
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(
        "REWORK: /rework > cookie .txt bhejo\n"
        "Supported formats:\n"
        "  X/Twitter:   [plus] [2fa false] [email].txt\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
        "  Reddit:      [1500 karma] [verified true] [username].txt\n\n"
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
    if uid := update.effective_user.id:
        if uid not in ADMIN_IDS:
            await update.message.reply_text("Sirf admin.")
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
    await update.message.reply_text(proxy_status_text())


async def netcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text(
        "Netcheck: Facebook + Instagram + Supabase reachability test\n"
        "Coming soon in next build."
    )


async def rework_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_state[uid] = {"mode": "rework", "platform": None}
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
    user_state[uid] = {"mode": "verify"}
    await update.message.reply_text(
        "Mode = verify. Ab cookie zip bhejo.\n"
        "Supported formats:\n"
        "  Instagram:   [7 followers] [2 posts] [verified false] [business false] [username].txt\n"
        "  Facebook:    [37 friends] [business false] [marketplace false] [] [userid].txt\n"
    )


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /live on|off")
        return
    val = args[0].lower()
    if val in ("on", "1", "yes"):
        user_state[uid] = user_state.get(uid, {})
        user_state[uid]["live"] = True
        await update.message.reply_text("Live = ON")
    elif val in ("off", "0", "no"):
        user_state[uid] = user_state.get(uid, {})
        user_state[uid]["live"] = False
        await update.message.reply_text("Live = OFF")
    else:
        await update.message.reply_text("Usage: /live on|off")


async def db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sirf admin.")
        return
    args = context.args
    sub = args[0].lower() if args else "stats"

    if not _db:
        await update.message.reply_text("SUPABASE_URL / KEY set nahi.")
        return

    import httpx
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
        cfg["name"] = (" ".join(args[1:]) or "camp")[:40]
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
            f"Role text mode. Variants bhejo.\n/camp idle se band."
        )
        return

    if sub == "idle":
        cfg["stage"] = "idle"
        save_camp(uid)
        await update.message.reply_text("Band.")
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
            await update.message.reply_text(f"Variant {i+1}: {t}")
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
        cfg["stage_at"] = asyncio.get_event_loop().time()
        save_camp(uid)
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
    if uid in CAMPRUN:
        CAMPRUN[uid]["stop"] = True
        await update.message.reply_text("Stop - current account ke baad rukega.")
    else:
        await update.message.reply_text("Koi campaign nahi.")


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

    try:
        file = await context.bot.get_file(doc.file_id)
        tmp = tempfile.mkdtemp(prefix="sm_")
        path = os.path.join(tmp, doc.file_name)
        await file.download_to_drive(path)

        results = []

        if doc.file_name.lower().endswith(".zip"):
            with _zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                for name in names[:MAX_FILES]:
                    raw = z.read(name).decode("utf-8", "ignore")
                    result = await _process_one(name, raw, state, context.bot)
                    results.append(result)
        else:
            with open(path, encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            result = await _process_one(doc.file_name, raw, state, context.bot)
            results.append(result)

        # Create output zip
        out_path = os.path.join(tmp, "out.zip")
        with _zipfile.ZipFile(out_path, "w", _zipfile.ZIP_DEFLATED) as zout:
            for name, output, status in results:
                folder = "working" if status == "LIVE" else ("dead" if status == "DEAD" else "unknown")
                zout.writestr(f"{folder}/{name}", output)

        await context.bot.send_document(
            update.effective_chat.id,
            document=open(out_path, "rb"),
            filename="reworked.zip",
            caption=f"Rework complete | {len(results)} files",
        )
        await msg.edit_text("Complete")

    except Exception as e:
        logger.exception("handle_file")
        await msg.edit_text(f"Error: {str(e)}")


async def _process_one(name: str, raw: str, state: dict, bot) -> tuple:
    """Process a single cookie file."""
    # Detect platform from filename
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

    # Save to DB if live and DB enabled
    if status == "LIVE" and _db and state.get("save_db", False):
        import httpx
        async with httpx.AsyncClient() as cl:
            table = "fb_accounts" if platform == "facebook" else "ig_accounts"
            row = {
                "username": meta.get("username", ""),
                "fb_id": meta.get("fb_id", "") if platform == "facebook" else "",
                "ig_id": meta.get("ig_id", "") if platform == "instagram" else "",
                "cookies": {k: v for k, v in (meta.get("_cookies", {}) or {}).items()},
                "country": meta.get("country", ""),
                "followers": int(meta.get("Followers", 0) or 0),
                "verified": str(meta.get("Verified", "false")).lower() == "true",
                "status": "live",
                "source": "bot",
            }
            await _db.upsert(cl, table, [row])

    return (name, output, status)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_state.pop(uid, None)
    await update.message.reply_text("Cancel / reset.")
