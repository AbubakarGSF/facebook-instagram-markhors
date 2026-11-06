"""Supabase database client for Social Markhors Bot.

Alag tables Facebook aur Instagram ke liye:
  - fb_accounts
  - ig_accounts
"""

import logging
from typing import Any, Dict, List, Optional, Tuple


import httpx

logger = logging.getLogger(__name__)


class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.enabled = bool(url and key)

    def table_url(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    async def count(self, client: httpx.AsyncClient, table: str, status: Optional[str] = None) -> int:
        try:
            params = {"select": "id", "limit": "1"}
            if status:
                params["status"] = f"eq.{status}"
            r = await client.get(
                self.table_url(table),
                params=params,
                headers={**self.headers, "Prefer": "count=exact"},
            )
            cr = r.headers.get("content-range", "")
            tail = cr.rsplit("/", 1)[-1] if "/" in cr else ""
            return int(tail) if tail.isdigit() else 0
        except Exception:
            logger.exception("supabase count")
            return 0

    async def upsert(self, client: httpx.AsyncClient, table: str, rows: List[Dict[str, Any]]) -> Tuple[int, Optional[str]]:
        """Upsert rows. Returns (ok_count, error_message)."""
        if not self.enabled or not rows:
            return 0, "db off" if rows else None

        # Same username/id conflict handling
        uniq: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            uname = row.get("username") or row.get("fb_id") or row.get("ig_id")
            if uname:
                uniq[str(uname)] = row
        rows = list(uniq.values())
        if not rows:
            return 0, "no username"

        try:
            r = await client.post(
                self.table_url(table),
                params={"on_conflict": "username"},
                headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=rows,
            )
            if r.status_code < 300:
                return len(rows), None
            logger.warning("supabase upsert %s %s", r.status_code, r.text[:200])
            return 0, f"HTTP {r.status_code}"
        except Exception:
            logger.exception("supabase upsert")
            return 0, "exception"

    async def patch(self, client: httpx.AsyncClient, table: str, username: str, payload: Dict[str, Any]) -> bool:
        try:
            r = await client.patch(
                self.table_url(table),
                params={"username": f"eq.{username}"},
                headers={**self.headers, "Prefer": "return=minimal"},
                json=payload,
            )
            return r.status_code < 300
        except Exception:
            logger.exception("supabase patch")
            return False

    async def delete(self, client: httpx.AsyncClient, table: str, usernames: List[str]) -> int:
        if not usernames:
            return 0
        ok = 0
        for i in range(0, len(usernames), 100):
            chunk = usernames[i : i + 100]
            try:
                r = await client.delete(
                    self.table_url(table),
                    params={"username": "in." + ",".join(chunk)},
                    headers={**self.headers, "Prefer": "return=minimal"},
                )
                if r.status_code < 300:
                    ok += len(chunk)
            except Exception:
                logger.exception("supabase delete")
        return ok

    async def fetch(self, client: httpx.AsyncClient, table: str, params: dict, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        try:
            p = dict(params)
            p["select"] = "*"
            p["limit"] = str(limit)
            p["offset"] = str(offset)
            p.setdefault("order", "id.asc")
            r = await client.get(self.table_url(table), params=p, headers=self.headers)
            if r.status_code >= 300:
                logger.warning("supabase fetch %s %s", r.status_code, r.text[:200])
                return []
            return r.json() or []
        except Exception:
            logger.exception("supabase fetch")
            return []
