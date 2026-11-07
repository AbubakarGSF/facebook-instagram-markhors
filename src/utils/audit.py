"""Audit logging for admin actions.

Records all admin actions with timestamp, user ID, action type, and details
for security auditing and compliance.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", "audit.log")
MAX_AUDIT_LOG_SIZE = int(os.getenv("MAX_AUDIT_LOG_SIZE", str(10 * 1024 * 1024)))  # 10MB


async def audit_log(uid: int, action: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Log an admin action for audit purposes.

    Args:
        uid: Telegram user ID of the admin performing the action
        action: Action name (e.g., 'proxy_add', 'db_export', 'camp_start')
        details: Additional details about the action (will be JSON-serialized)
    """
    entry = {
        "ts": time.time(),
        "uid": uid,
        "action": action,
        "details": details or {},
    }

    try:
        log_line = json.dumps(entry, ensure_ascii=False)
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

        # Rotate if file exceeds MAX_AUDIT_LOG_SIZE
        if os.path.getsize(AUDIT_LOG_FILE) > MAX_AUDIT_LOG_SIZE:
            _rotate_audit_log()

    except Exception:
        logging.exception("Failed to write audit log entry")


def _rotate_audit_log() -> None:
    """Rotate audit log when it exceeds MAX_AUDIT_LOG_SIZE."""
    try:
        audit_file = AUDIT_LOG_FILE
        if os.path.exists(audit_file):
            rotated = audit_file + ".1"
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(audit_file, rotated)
    except Exception:
        logging.exception("Failed to rotate audit log")


def get_audit_logs(limit: int = 50) -> list:
    """Retrieve the most recent audit log entries.

    Args:
        limit: Maximum number of entries to return

    Returns:
        List of audit log entries as dicts
    """
    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return []
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
    except Exception:
        logging.exception("Failed to read audit logs")
        return []


def audit_summary() -> Dict[str, int]:
    """Get summary statistics from audit logs.

    Returns:
        Dict with action counts
    """
    counts: Dict[str, int] = {}
    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return counts
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        action = entry.get("action", "unknown")
                        counts[action] = counts.get(action, 0) + 1
                    except json.JSONDecodeError:
                        continue
    except Exception:
        logging.exception("Failed to compute audit summary")
    return counts
