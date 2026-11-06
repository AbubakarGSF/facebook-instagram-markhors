"""Telegram bot handlers for Social Markhors Bot.

Admin commands: /start, /help, /status, /diag, /proxy, /netcheck, /rework,
/verify, /live, /camp, /db, /db stats, /db export, /db reverify
"""

import asyncio
import logging
import os
import tempfile
import zipfile as _zipfile
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, CREDIT, WEBSITE, MAX_FILES
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
        "Admin: /db /proxy /netcheck"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fb = "ON" if _fb_platform else "OFF"
    ig = "ON" if _ig_platform else "OFF"
    await update.message.reply_text(
        f"Social Markhors Bot\n"
        f"Credit: {CREDIT}\n"
        f"Web: {WEBSITE}\n"
        f"Admin: {is_admin(uid)}\n"
        f"Facebook: {fb}\n"
        f"Instagram: {ig}\n"
        f"DB: {'ON' if _db else 'OFF'}"
    )


async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text(
        f"diag: system ok\n"
        f"fb_platform: {'ok' if _fb_platform else 'none'}\n"
        f"ig_platform: {'ok' if _ig_platform else 'none'}\n"
        f"db: {'ok' if _db else 'none'}"
    )


async def proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text(proxy_status_text())


async def netcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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


async def db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    args = context.args
    sub = args[0].lower() if args else "stats"

    if not _db:
        await update.message.reply_text("SUPABASE_URL / KEY set nahi.")
        return

    # Create a temp client for DB operations
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


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookie file uploads for rework/verify."""
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
    return (name, output, status)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_state.pop(uid, None)
    await update.message.reply_text("Cancel / reset.")
