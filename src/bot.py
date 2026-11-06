"""Telegram bot handlers for Social Markhors Bot.

Admin commands: /start, /help, /status, /proxy, /netcheck, /rework, /db
"""

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, CREDIT, WEBSITE


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Social Markhors Bot\n\nModes:\n1) FACEBOOK rework + verify\n2) INSTAGRAM rework + verify\n\n"
        "/rework\n/status\n/diag\n/netcheck\n/proxy\n\nAdmin: /db\n\nFull commands: /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "REWORK: /rework > cookie .txt bhejo\n"
        "Supported: [plus] [2fa false] [email].txt (X/Twitter)\n"
        "         [7 followers] [2 posts] [verified false] [business false] [username].txt (Instagram)\n"
        "         [37 friends] [business false] [marketplace false] [] [userid].txt (Facebook)\n\n"
        "VERIFY: /verify > zip\nDB: /db stats | /db export\n\nAll: /help"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Social Markhors Bot\nCredit: {CREDIT}\nWeb: {WEBSITE}\nAdmin: {is_admin(update.effective_user.id)}")


async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text("diag: system ok")


async def proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text("Proxy status: check karo")


async def netcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Sirf admin.")
        return
    await update.message.reply_text("Netcheck: X/FB/IG reachability")


async def rework_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_cfg(update.effective_user.id, mode="rework")
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
    await update.message.reply_text("/db stats | /db export fb | /db export ig")


async def set_cfg(uid: int, **kw):
    pass  # placeholder - real state in main.py
