"""Facebook platform module.

Live check, post, and cookie rework for Facebook accounts.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import BasePlatform
from src.utils.ratelimit import rate_limiter

logger = logging.getLogger(__name__)


class FacebookPlatform(BasePlatform):
    NAME = "facebook"

    # Facebook check endpoints
    CHECK_URL = "https://www.facebook.com/"
    MOBILE_CHECK_URL = "https://mbasic.facebook.com/"
    POST_URL = "https://www.facebook.com/"

    async def live_check_impl(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Facebook cookie se check karo ki account working hai ya dead."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            r = await client.get(self.MOBILE_CHECK_URL, headers=headers)

            if r.status_code == 200:
                html = r.text

                # Check if redirected to login (cookie dead)
                if "login" in r.url.lower() or "log in" in html.lower()[:500]:
                    return {
                        "Status": "DEAD",
                        "Platform": "facebook",
                        "Reason": "redirected to login",
                    }

                # Try to extract username
                uname_match = re.search(
                    r'(?:name|title)[^>]*>([^<]+)</', html, re.I
                )
                username = uname_match.group(1).strip() if uname_match else ""

                # Try to extract user id from page
                fb_id_match = re.search(r'userID["\']?\s*[:=]\s*["\']?(\d+)', html)
                fb_id = fb_id_match.group(1) if fb_id_match else ""

                return {
                    "Status": "LIVE",
                    "Platform": "facebook",
                    "username": username,
                    "fb_id": fb_id,
                    "ig_id": "",
                    "Reason": "",
                }

            if r.status_code in (302, 301):
                # Redirect usually means login required
                location = r.headers.get("location", "")
                if "login" in location.lower():
                    return {
                        "Status": "DEAD",
                        "Platform": "facebook",
                        "Reason": "login redirect",
                    }

            if r.status_code in (401, 403):
                return {
                    "Status": "DEAD",
                    "Platform": "facebook",
                    "Reason": "auth failed (401/403)",
                }

            return {
                "Status": "UNKNOWN",
                "Platform": "facebook",
                "Reason": f"HTTP {r.status_code}",
            }

        except httpx.ConnectError as e:
            return {
                "Status": "UNKNOWN",
                "Platform": "facebook",
                "Reason": f"connect error: {type(e).__name__}",
            }
        except Exception as e:
            logger.exception("fb live check")
            return {
                "Status": "UNKNOWN",
                "Platform": "facebook",
                "Reason": str(e)[:80],
            }

    async def post(self, client: httpx.AsyncClient, cookies: Dict[str, str], text: str, media: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str]]:
        """Facebook par post publish karo. Rate limit handling with exponential backoff."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.facebook.com",
            "Referer": "https://www.facebook.com/",
        }

        payload = {
            "status_text": text,
        }

        max_retries = 5

        for attempt in range(max_retries):
            try:
                r = await client.post(self.POST_URL, headers=headers, data=payload)

                if r.status_code == 200:
                    # Check if post succeeded
                    if "Post published" in r.text or "story" in r.url.lower():
                        return True, None, None
                    return True, None, None

                if r.status_code == 429:
                    # Rate limited - wait and retry
                    retry_after = None
                    retry_after_header = r.headers.get("retry-after") or r.headers.get("Retry-After")
                    if retry_after_header:
                        try:
                            retry_after = int(retry_after_header)
                        except ValueError:
                            retry_after = None
                    wait = rate_limiter.record_response("facebook", 429, retry_after)
                    if wait:
                        await asyncio.sleep(wait)
                        continue
                    return False, None, "RATE"

                if r.status_code in (401, 403):
                    return False, None, "DEAD"

                return False, None, f"HTTP {r.status_code}"

            except httpx.ConnectError as e:
                return False, None, "NET"
            except Exception as e:
                logger.exception("fb post")
                return False, None, str(e)[:80]

        return False, None, "RATE_MAX_RETRIES"