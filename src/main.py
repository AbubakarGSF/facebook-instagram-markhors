"""Entry point for Social Markhors Bot."""

import asyncio
import json
import logging
import os
import sys

# .env MUST be loaded before config.settings is imported (settings reads env at
# import time; ENCRYPTION_KEY validation would otherwise exit on local runs).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters  # noqa: E402

from config.settings import (  # noqa: E402
    ADMIN_IDS,
    BOT_TOKEN,
    BOT_VERSION,
    HEALTH_CHECK_HOST,
    HEALTH_CHECK_PORT,
    NETCHECK_ON_START,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from src.bot import (  # noqa: E402
    CAMPRUN,
    cancel_cmd,
    camp_cmd,
    db_cmd,
    diag_cmd,
    handle_file,
    help_cmd,
    init_db,
    init_platforms,
    live_cmd,
    load_camps,
    netcheck_cmd,
    on_media,
    on_text,
    proxy_cmd,
    rework_cmd,
    run_netcheck,
    start,
    status_cmd,
    stopcamp_cmd,
    verify_cmd,
)
from src.utils.proxy import check_proxy_readiness, load_proxies  # noqa: E402

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    """JSON log lines that stay valid even when messages contain quotes."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

_health_server = None


async def _health_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Minimal HTTP health endpoint (no secrets, safe for any peer)."""
    try:
        data = await reader.read(1024)
        if not data:
            return
        request_line = data.split(b"\n")[0].decode("utf-8", errors="ignore")
        parts = request_line.split()
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""

        if method in ("GET", "HEAD") and path.split("?")[0] in ("/healthz", "/"):
            body = json.dumps(
                {"status": "ok", "bot": "social-markhors", "version": BOT_VERSION}
            ).encode()
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
                + (body if method == "GET" else b"")
            )
        else:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

        writer.write(response)
        await writer.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def start_health_server():
    """Start HTTP health check server if HEALTH_CHECK_PORT is set."""
    global _health_server
    if HEALTH_CHECK_PORT <= 0:
        logger.info("Health check server disabled (HEALTH_CHECK_PORT=0)")
        return
    try:
        _health_server = await asyncio.start_server(
            _health_handler, HEALTH_CHECK_HOST, HEALTH_CHECK_PORT
        )
        logger.info("Health check server on %s:%s", HEALTH_CHECK_HOST, HEALTH_CHECK_PORT)
    except Exception:  # noqa: BLE001
        logger.exception("Health check server failed to start")


async def stop_health_server():
    """Stop health check server."""
    global _health_server
    if _health_server is not None:
        _health_server.close()
        try:
            await _health_server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        _health_server = None


async def post_init(app):
    """Initialize DB, platforms, campaigns, proxies and health server."""
    db = init_db(SUPABASE_URL, SUPABASE_KEY)
    if db:
        logger.info("Supabase DB initialized")
    else:
        logger.warning("Supabase DB OFF (SUPABASE_URL / SUPABASE_KEY missing)")

    init_platforms()
    load_camps()
    logger.info("Platforms initialized (Facebook, Instagram)")

    proxy_count = len(load_proxies())
    logger.info("Proxies loaded: %s", proxy_count)
    for w in check_proxy_readiness():
        logger.warning(w)

    await start_health_server()

    if NETCHECK_ON_START:
        try:
            report = await run_netcheck()
            logger.info("startup netcheck:\n%s", report)
        except Exception:  # noqa: BLE001
            logger.exception("startup netcheck failed")


async def on_error(update, context):
    """Log handler crashes instead of failing silently (admins ko short hint)."""
    logger.error("handler error: %s", context.error, exc_info=context.error)
    try:
        user = getattr(update, "effective_user", None)
        if user is not None and user.id in ADMIN_IDS:
            msg = getattr(update, "effective_message", None)
            if msg is not None:
                await msg.reply_text(
                    f"Error: {type(context.error).__name__}. Logs check karo."
                )
    except Exception:  # noqa: BLE001
        pass


async def post_shutdown(app):
    """Graceful shutdown: stop campaigns, close health server."""
    logger.info("Bot shutting down...")
    for state in CAMPRUN.values():
        state["stop"] = True
    await stop_health_server()


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
        ("verify", verify_cmd),
        ("live", live_cmd),
        ("camp", camp_cmd),
        ("stopcamp", stopcamp_cmd),
        ("db", db_cmd),
        ("cancel", cancel_cmd),
    ]
    for cmd, fn in handlers:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_error_handler(on_error)

    # Cookie zip/txt uploads
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    # Campaign media collection
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, on_media))
    # Text handler for campaign text collection
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Social Markhors Bot v%s running | admins: %s", BOT_VERSION, len(ADMIN_IDS))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
