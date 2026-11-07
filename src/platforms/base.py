"""Base platform class for Facebook and Instagram.

Shared logic: proxy rotation, cookie parsing, rate limiting, error classification.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


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
        """Mask cookie values in a cookie string for logging."""
        if not cookie_str:
            return cookie_str
        # Mask value part of each cookie=value pair
        masked = re.sub(r'([a-zA-Z_][a-zA-Z0-9_]*)=([^;]*)', r'\1=***masked***', cookie_str)
        return masked

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
            httponly = line.startswith("#HttpOnly_")
            if httponly:
                line = line[len("#HttpOnly_"):]
            elif not line.strip() or line.strip().startswith("#"):
                continue

            # Netscape tab-separated: domain\tflag\tpath\tsecure\texpire\tname\tvalue
            if "\t" in line and len(line.split("\t")) >= 7:
                parts = line.split("\t")
                domain = parts[0].strip() or ".domain"
                flag = parts[1].strip() or "TRUE"
                cpath = parts[2].strip() or "/"
                secure = parts[3].strip() or "TRUE"
                expiry = parts[4].strip()
                if not expiry.isdigit():
                    expiry = str(exp)
                name = parts[5].strip()
                val = parts[6].strip()
                if name and val and name not in seen:
                    seen.add(name)
                    data[name] = val
                    cookie = f"{domain}\t{flag}\t{cpath}\t{secure}\t{expiry}\t{name}\t{val}"
                    if httponly:
                        cookie = "#HttpOnly_" + cookie
                    cookies.append(cookie)
                continue

            # key=value or key: value (strict — header lines aur URLs skip)
            m = re.match(r"^([A-Za-z0-9_.\-]{1,60})\s*(=|:)\s*(.+)$", line)
            if not m:
                continue
            k = m.group(1).strip()
            sep = m.group(2)
            v = m.group(3).strip().strip('"').strip("'")
            if not k or not v or k in seen:
                continue
            if sep == ":" and (v.startswith("//") or " " in v):
                continue
            seen.add(k)
            data[k] = v
            cookie = f".domain\tTRUE\t/\tTRUE\t{exp}\t{k}\t{v}"
            if httponly:
                cookie = "#HttpOnly_" + cookie
            cookies.append(cookie)

        return data, cookies

    async def live_check(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Check if cookie is working. Returns dict with Status, Reason, and platform fields."""
        return await self._check_with_retries(client, cookies, retries=self.retries)

    async def _check_with_retries(self, client: httpx.AsyncClient, cookies: Dict[str, str], retries: int = 3) -> Dict[str, Any]:
        """Run live_check with exponential backoff retries."""
        last_result = None
        for attempt in range(retries):
            try:
                result = await self._check_single(client, cookies)
            except Exception as e:
                logging.exception(f"live_check exception on attempt {attempt + 1}: {e}")
                result = {
                    "Status": "UNKNOWN",
                    "Platform": self.__class__.__name__,
                    "Reason": f"exception: {str(e)[:80]}",
                }

            last_result = result

            # If LIVE, return immediately
            if result.get("Status") == "LIVE":
                return result

            # If not last attempt, wait before retry
            if attempt < retries - 1:
                wait_time = min(2 ** attempt, 30)  # Exponential backoff: 1s, 2s, 4s... max 30s
                self.logger.info(f"Verify attempt {attempt + 1} failed: {result.get('Reason', 'unknown')}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        return last_result or {"Status": "UNKNOWN", "Platform": self.__class__.__name__, "Reason": "all retries failed"}

    async def _check_single(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Wrapper around live_check that subclasses implement."""
        return await self.live_check_impl(client, cookies)

    async def live_check_impl(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Actual implementation - subclasses must implement this."""
        raise NotImplementedError

    async def _run_check(self, client: httpx.AsyncClient, cookies: Dict[str, str], retries: int = 2) -> Optional[Dict[str, Any]]:
        """Run a check and swallow exceptions so the fallback can continue."""
        try:
            return await self._check_with_retries(client, cookies, retries=retries)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s check failed: %s", self.NAME, e)
            return None

    async def check_with_fallback(
        self,
        client_direct: Optional[httpx.AsyncClient],
        client_proxy: Optional[httpx.AsyncClient],
        cookies: Dict[str, str],
        proxy_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Try direct first, then proxy (ya ulta), best result return karo.

        Order:
          - proxy available ho to pehle proxy (asli target wahi hai), phir direct.
          - proxy fail ho jaye network-wise to direct fallback (Railway IP).
        """
        last: Optional[Dict[str, Any]] = None

        candidates = []
        if client_proxy is not None:
            candidates.append(("proxy", client_proxy))
        if client_direct is not None:
            candidates.append(("direct", client_direct))

        for label, client in candidates:
            result = await self._run_check(client, cookies, retries=2)
            if not result:
                continue
            if result.get("Status") == "LIVE":
                result["via"] = label
                return result
            if result.get("Status") == "DEAD":
                # Cookie genuinely dead — dusra route try karne ka faida nahi.
                result["via"] = label
                return result
            last = result

        if last:
            last.setdefault("via", "none")
            last["Reason"] = f"all routes failed: {last.get('Reason', 'unknown')}"
            return last

        return {
            "Status": "UNKNOWN",
            "Platform": self.NAME,
            "Reason": "both direct and proxy failed",
            "via": "none",
        }
