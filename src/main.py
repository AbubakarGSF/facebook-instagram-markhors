"""Entry point for Social Markhors Bot."""

import asyncio
import logging
import os
import sys

# Add project root to path so config package can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    on_text,
    proxy_cmd,
    rework_cmd,
    start,
    status_cmd,
    stopcamp_cmd,
    verify_cmd,
)
from src.utils.proxy import check_proxy_readiness, load_proxies

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

_health_server = None


def _is_admin_ip(host: str) -> bool:
    """Check if request comes from localhost or admin IP."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    return False


async def _health_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Simple HTTP health check endpoint - localhost only."""
    try:
        data = await reader.read(1024)
        if not data:
            writer.close()
            return
        request_line = data.split(b"\n")[0].decode("utf-8", errors="ignore")
        parts = request_line.split()
        method = parts[0] if len(parts) > 0 else ""
        path = parts[1] if len(parts) > 1 else ""

        if method == "GET" and path == "/healthz":
            body = b'{"status":"ok"}'
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                + body
            )
        else:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

        # Only allow localhost connections for health check
        peername = writer.get_extra_info("peername")
        host = peername[0] if peername else ""
        if host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server():
    """Start HTTP health check server if HEALTH_CHECK_PORT is set."""
    global _health_server
    if HEALTH_CHECK_PORT <= 0:
        return
    try:
        _health_server = await asyncio.start_server(
            _health_handler, HEALTH_CHECK_HOST, HEALTH_CHECK_PORT
        )
        logger.info(f"Health check server on {HEALTH_CHECK_HOST}:{HEALTH_CHECK_PORT}")
    except Exception:
        logger.exception("Health check server failed to start")


async def stop_health_server():
    """Stop health check server."""
    global _health_server
    if _health_server is not None:
        _health_server.close()
        try:
            await _health_server.wait_closed()
        except Exception:
            pass
        _health_server = None


async def post_init(app):
    """Initialize DB and platforms after bot starts."""
    # Initialize DB
    db = init_db(SUPABASE_URL, SUPABASE_KEY)
    if db:
        logger.info("Supabase DB initialized")

    # Initialize platforms
    init_platforms()
    logger.info("Platforms initialized (Facebook, Instagram)")

    # Load proxies
    proxy_count = len(load_proxies())
    logger.info(f"Proxies loaded: {proxy_count}")

    # Check proxy readiness
    proxy_warnings = check_proxy_readiness()
    for w in proxy_warnings:
        logger.warning(w)

    # Start health check server
    await start_health_server()

    if NETCHECK_ON_START:
        logger.info("Netcheck on start enabled")


async def post_shutdown(app):
    """Graceful shutdown: close health server, cancel running tasks."""
    logger.info("Bot shutting down...")

    # Stop health server
    await stop_health_server()

    # Cancel running campaigns
    for uid, state in CAMPRUN.items():
        state["stop"] = True


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
    # Text handler for campaign text collection
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )

    logger.info("Social Markhors Bot running | admins: %s", len(ADMIN_IDS))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    load_dotenv()
    main()