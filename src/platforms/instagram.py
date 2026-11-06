"""Instagram platform module.

Live check, post, and cookie rework for Instagram accounts.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import BasePlatform

logger = logging.getLogger(__name__)


class InstagramPlatform(BasePlatform):
    NAME = "instagram"

    # Instagram check endpoints
    CHECK_URL = "https://www.instagram.com/"
    MOBILE_CHECK_URL = "https://www.instagram.com/accounts/edit/"

    async def live_check(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Instagram cookie se check karo ki account working hai ya dead."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            r = await client.get(self.CHECK_URL, headers=headers)

            if r.status_code == 200:
                html = r.text

                # Check if redirected to login (cookie dead)
                if "login" in r.url.lower() or '"is_logged_in":false' in html:
                    return {
                        "Status": "DEAD",
                        "Platform": "instagram",
                        "Reason": "redirected to login or not logged in",
                    }

                # Try to extract username from page
                uname_match = re.search(
                    r'"username"\s*:\s*"([^"]+)"', html
                )
                username = uname_match.group(1) if uname_match else ""

                # Try to extract user id
                ig_id_match = re.search(r'"pk"\s*:\s*"?(\d+)"?', html)
                ig_id = ig_id_match.group(1) if ig_id_match else ""

                return {
                    "Status": "LIVE",
                    "Platform": "instagram",
                    "username": username,
                    "ig_id": ig_id,
                    "fb_id": "",
                    "Reason": "",
                }

            if r.status_code in (302, 301):
                location = r.headers.get("location", "")
                if "login" in location.lower() or "accounts/login" in location.lower():
                    return {
                        "Status": "DEAD",
                        "Platform": "instagram",
                        "Reason": "login redirect",
                    }

            if r.status_code in (401, 403):
                return {
                    "Status": "DEAD",
                    "Platform": "instagram",
                    "Reason": "auth failed (401/403)",
                }

            return {
                "Status": "UNKNOWN",
                "Platform": "instagram",
                "Reason": f"HTTP {r.status_code}",
            }

        except httpx.ConnectError as e:
            return {
                "Status": "UNKNOWN",
                "Platform": "instagram",
                "Reason": f"connect error: {type(e).__name__}",
            }
        except Exception as e:
            logger.exception("ig live check")
            return {
                "Status": "UNKNOWN",
                "Platform": "instagram",
                "Reason": str(e)[:80],
            }

    async def post(self, client: httpx.AsyncClient, cookies: Dict[str, str], text: str, media: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str]]:
        """Instagram par post publish karo."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
        }

        payload = {
            "caption": text,
        }

        try:
            r = await client.post(
                "https://www.instagram.com/api/v1/web/posts/create/",
                headers=headers,
                data=payload,
            )

            if r.status_code == 200:
                return True, None, None

            if r.status_code == 429:
                return False, None, "RATE"

            if r.status_code in (401, 403):
                return False, None, "DEAD"

            return False, None, f"HTTP {r.status_code}"

        except httpx.ConnectError as e:
            return False, None, "NET"
        except Exception as e:
            logger.exception("ig post")
            return False, None, str(e)[:80]
