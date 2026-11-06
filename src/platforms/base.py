"""Base platform class for Facebook and Instagram.

Shared logic: proxy rotation, cookie parsing, rate limiting, error classification.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx


class BasePlatform:
    """Every platform (facebook, instagram) inherits from this."""

    NAME = "base"

    def __init__(self, concurrency: int = 8, timeout: float = 25.0, retries: int = 3):
        self.concurrency = concurrency
        self.timeout = timeout
        self.retries = retries
        self.logger = logging.getLogger(self.__class__.__name__)

    def http_client(self, proxy: Optional[str] = None) -> httpx.AsyncClient:
        """httpx client with optional proxy. Telegram/Supabase never use proxy."""
        kw = dict(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)),
            limits=httpx.Limits(
                max_connections=max(self.concurrency * 2, 10),
                max_keepalive_connections=max(self.concurrency, 10),
            ),
            trust_env=False,
        )
        if proxy:
            kw["proxy"] = proxy
        return httpx.AsyncClient(**kw)

    def mask_cookie(self, cookie_str: str) -> str:
        """Cookie string mein se credentials chhupao."""
        return "***masked***"

    def parse_cookie_file(self, text: str) -> Tuple[Dict[str, str], List[str]]:
        """Cookie file se cookies extract karo.

        Supports:
          - Netscape tab-separated (#HttpOnly_ prefix optional)
          - key=value lines
          - key: value lines

        Returns (metadata_dict, cookie_lines_list).
        """
        data: Dict[str, str] = {}
        cookies: List[str] = []
        seen = set()
        exp = int(time.time()) + 86400 * 30

        for line in (text or "").splitlines():
            line = line.rstrip("\r")
            if not line.strip() or line.strip().startswith("#"):
                continue

            # Netscape tab-separated: domain\tflag\tpath\tsecure\texpire\tname\tvalue
            if "\t" in line and len(line.split("\t")) >= 7:
                parts = line.split("\t")
                name = parts[5].strip()
                val = parts[6].strip()
                if name and val and name not in seen:
                    seen.add(name)
                    cookies.append(
                        f".domain\tTRUE\t/\tTRUE\t{exp}\t{name}\t{val}"
                    )
                continue

            # key=value or key: value
            for sep in ("=", ":"):
                if sep in line:
                    k, v = line.split(sep, 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and v and k not in seen:
                        seen.add(k)
                        data[k] = v
                        cookies.append(
                            f".domain\tTRUE\t/\tTRUE\t{exp}\t{k}\t{v}"
                        )
                    break

        return data, cookies

    async def live_check(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Check if cookie is working. Returns dict with Status, Reason, and platform fields."""
        raise NotImplementedError

    async def post(self, client: httpx.AsyncClient, cookies: Dict[str, str], text: str, media: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str]]:
        """Post content. Returns (ok, post_id, error)."""
        raise NotImplementedError
