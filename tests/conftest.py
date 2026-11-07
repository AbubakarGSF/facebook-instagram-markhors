"""Conftest: project root on path, mandatory env vars, telegram stubs.

`python-telegram-bot` is an optional test dependency (it is installed in
production). Stubbing it here lets unit tests import `src.bot` helpers without
pulling the whole Telegram stack.
"""
import os
import sys
import types

from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# config.settings exits the process when ENCRYPTION_KEY is missing.
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("VERIFY_SLOW_RETRY_SEC", "0")
os.environ.setdefault("USER_CMD_COOLDOWN", "0")
os.environ.setdefault("CONTENT_COOLDOWN", "0")

try:  # real package if installed
    import telegram  # noqa: F401
    import telegram.ext  # noqa: F401
except ImportError:  # pragma: no cover - local dev environment
    tg = types.ModuleType("telegram")

    class Update:  # minimal stand-in
        pass

    tg.Update = Update
    te = types.ModuleType("telegram.ext")

    class ContextTypes:
        DEFAULT_TYPE = object

    te.ContextTypes = ContextTypes
    sys.modules["telegram"] = tg
    sys.modules["telegram.ext"] = te
