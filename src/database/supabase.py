"""Supabase database client for Social Markhors Bot.

Alag tables Facebook aur Instagram ke liye:
  - fb_accounts
  - ig_accounts
"""

import logging
import re
import time
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

    @staticmethod
    def _id_field(row: Dict[str, Any]) -> Tuple[str, str]:
        """Return (column, value) for the platform id, or ("", "")."""
        for col in ("fb_id", "ig_id"):
            val = row.get(col)
            if val:
                return col, str(val)
        return "", ""

    # Optional campaign columns. Purane deployments me ye columns nahi hote;
    # upsert pehle full payload bhejta hai aur sirf missing-column error par
    # inhe strip karke retry karta hai (schema apply karte hi data save hone lagega).
    OPTIONAL_COLUMNS = ("year", "media_url")

    @classmethod
    def _missing_columns(cls, error_text: str) -> set:
        text = (error_text or "").lower()
        if not text or ("column" not in text and "schema cache" not in text):
            return set()
        # PostgREST ek hi missing column ka naam deta hai; baqi optional columns
        # bhi purane schema me missing ho sakte hain, is liye sab strip karo.
        if "schema cache" in text or "could not find the" in text:
            return set(cls.OPTIONAL_COLUMNS)
        return {
            col for col in cls.OPTIONAL_COLUMNS
            if re.search(rf"['\"]?{col}['\"]?", text)
        }

    async def _send_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        payload: Any,
        **kwargs: Any,
    ) -> httpx.Response:
        """POST/PATCH with one retry after stripping unknown optional columns."""
        fn = getattr(client, method)
        r = await fn(url, json=payload, **kwargs)
        if r.status_code < 300:
            return r
        missing = self._missing_columns(r.text)
        if not missing:
            return r
        if isinstance(payload, list):
            slim = [
                {k: v for k, v in row.items() if k not in missing}
                for row in payload
            ]
        else:
            slim = {k: v for k, v in payload.items() if k not in missing}
        logger.warning(
            "supabase: optional column(s) %s missing, retrying without them",
            sorted(missing),
        )
        return await fn(url, json=slim, **kwargs)

    async def upsert(self, client: httpx.AsyncClient, table: str, rows: List[Dict[str, Any]]) -> Tuple[int, Optional[str]]:
        """Upsert rows. Returns (ok_count, error_message).

        `username` is the conflict target, but it can be empty for Facebook
        exports that only carry a numeric ID. Empty usernames are filled from
        the platform id so distinct accounts do not collapse into one row.
        If the bulk upsert still conflicts on fb_id/ig_id, each row is patched
        by id (then by username) before falling back to an insert.
        """
        if not self.enabled or not rows:
            return 0, "db off" if rows else None

        cleaned: List[Dict[str, Any]] = []
        seen = set()
        for raw in rows:
            row = dict(raw)
            key = row.get("username") or row.get("fb_id") or row.get("ig_id")
            if not key:
                logger.warning("supabase upsert: row without username/id skipped")
                continue
            row["username"] = str(key)
            if row["username"] in seen:
                continue
            seen.add(row["username"])
            cleaned.append(row)

        if not cleaned:
            return 0, "no username"

        try:
            r = await self._send_json(
                client,
                "post",
                self.table_url(table),
                cleaned,
                params={"on_conflict": "username"},
                headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            if r.status_code < 300:
                return len(cleaned), None
            logger.warning("supabase bulk upsert %s %s", r.status_code, r.text[:200])
            bulk_error = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:  # noqa: BLE001
            logger.exception("supabase upsert")
            bulk_error = f"{type(e).__name__}: {e}"

        # Fallback: per-row patch/insert. Slower, but survives unique-id
        # conflicts that the bulk `username` upsert cannot express.
        ok = 0
        last_error = bulk_error
        for row in cleaned:
            if await self._upsert_row(client, table, row):
                ok += 1
            else:
                last_error = last_error or "row upsert failed"
        if ok:
            return ok, (None if ok == len(cleaned) else last_error)
        return 0, last_error

    @staticmethod
    def _json_body(r: httpx.Response) -> Any:
        """Empty/non-JSON body par crash na karo (200 + empty patch common hai)."""
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    async def _upsert_row(self, client: httpx.AsyncClient, table: str, row: Dict[str, Any]) -> bool:
        id_col, id_val = self._id_field(row)
        payload = dict(row)
        try:
            # 1) Existing row with same platform id -> patch it.
            if id_col:
                r = await self._send_json(
                    client,
                    "patch",
                    self.table_url(table),
                    payload,
                    params={id_col: f"eq.{id_val}"},
                    headers={**self.headers, "Prefer": "return=representation"},
                )
                if r.status_code < 300 and self._json_body(r):
                    return True
            # 2) Existing row with same username -> patch it.
            r = await self._send_json(
                client,
                "patch",
                self.table_url(table),
                payload,
                params={"username": f"eq.{row['username']}"},
                headers={**self.headers, "Prefer": "return=representation"},
            )
            if r.status_code < 300 and self._json_body(r):
                return True
            # 3) Brand-new row.
            r = await self._send_json(
                client,
                "post",
                self.table_url(table),
                [payload],
                headers={**self.headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
            )
            return r.status_code < 300
        except Exception:  # noqa: BLE001
            logger.exception("supabase row upsert")
            return False

    async def patch_status(
        self,
        client: httpx.AsyncClient,
        table: str,
        username: str,
        status: str,
        ok: bool,
    ) -> bool:
        """Update status/last_check/last_ok for an existing row (best effort)."""
        if not username:
            return False
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload: Dict[str, Any] = {"status": status, "last_check": now}
        if ok:
            payload["last_ok"] = now
        try:
            r = await client.patch(
                self.table_url(table),
                params={"username": f"eq.{username}"},
                headers={**self.headers, "Prefer": "return=minimal"},
                json=payload,
            )
            return r.status_code < 300
        except Exception:  # noqa: BLE001
            logger.exception("supabase patch_status")
            return False

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
