"""Entry point for Social Markhors Bot."""

import asyncio
import logging
import sys

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from config.settings import (
    ADMIN_IDS,
    BOT_TOKEN,
    CREDIT,
    HEALTH_CHECK_HOST,
    HEALTH_CHECK_PORT,
    NETCHECK_ON_START,
    SUPABASE_KEY,
    SUPABASE_URL,
    WEBSITE,
)
from src.bot import (
    cancel_cmd,
    camp_cmd,
    db_cmd,
    diag_cmd,
    handle_file,
    help_cmd,
    init_db,
    init_platforms,
    live_cmd,
    netcheck_cmd,
    proxy_cmd,
    rework_cmd,
    start,
    status_cmd,
    stopcamp_cmd,
    verify_cmd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def post_init(app):
    """Initialize DB and platforms after bot starts."""
    # Initialize DB
    db = init_db(SUPABASE_URL, SUPABASE_KEY)
    if db:
        logger.info("Supabase DB initialized")

    # Initialize platforms
    init_platforms()
    logger.info("Platforms initialized (Facebook, Instagram)")

    if NETCHECK_ON_START:
        logger.info("Netcheck on start enabled")


async def post_shutdown(app):
    logger.info("Bot shutting down...")


def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN":
        print("FATAL: BOT_TOKEN env zaroori hai. .env file set karo.")
        sys.exit(1)

    if not ADMIN_IDS:
        print("FATAL: ADMIN_IDS env zaroori hai.")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Command handlers
    handlers = [
        ("start", start),
        ("help", help_cmd),
        ("status", status_cmd),
        ("diag", diag_cmd),
        ("proxy", proxy_cmd),
        ("netcheck", netcheck_cmd),
        ("rework", rework_cmd),
        ("verify", verify_cmd),
        ("live", live_cmd),
        ("camp", camp_cmd),
        ("stopcamp", stopcamp_cmd),
        ("db", db_cmd),
        ("cancel", cancel_cmd),
    ]
    for cmd, fn in handlers:
        app.add_handler(CommandHandler(cmd, fn))

    # Document handler for cookie file uploads
    app.add_handler(
        MessageHandler(filters.Document.ALL, handle_file)
    )

    logger.info("Social Markhors Bot running | admins: %s", len(ADMIN_IDS))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    load_dotenv()
    main()
