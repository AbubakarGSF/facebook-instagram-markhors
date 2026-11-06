"""Entry point for Social Markhors Bot."""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler

from config.settings import (
    ADMIN_IDS,
    BOT_TOKEN,
    CREDIT,
    HEALTH_CHECK_HOST,
    HEALTH_CHECK_PORT,
    NETCHECK_ON_START,
    WEBSITE,
)
from src.bot import (
    db_cmd,
    diag_cmd,
    help_cmd,
    netcheck_cmd,
    proxy_cmd,
    rework_cmd,
    start,
    status_cmd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def post_init(app):
    logger.info("Bot starting...")
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

    handlers = [
        ("start", start),
        ("help", help_cmd),
        ("status", status_cmd),
        ("diag", diag_cmd),
        ("proxy", proxy_cmd),
        ("netcheck", netcheck_cmd),
        ("rework", rework_cmd),
        ("db", db_cmd),
    ]
    for cmd, fn in handlers:
        app.add_handler(CommandHandler(cmd, fn))

    logger.info("Social Markhors Bot running | admins: %s", len(ADMIN_IDS))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    load_dotenv()
    main()
