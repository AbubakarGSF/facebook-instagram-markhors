"""Facebook platform module.

Live check, post, and db helpers for Facebook accounts.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import BasePlatform

logger = logging.getLogger(__name__)


class FacebookPlatform(BasePlatform):
    NAME = "facebook"

    # Graph API endpoints
    ME_URL = "https://graph.facebook.com/me"
    FEED_URL = "https://graph.facebook.com/me/feed"

    async def live_check(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Facebook cookie se check karo ki account working hai ya dead."""

        # Convert cookie dict to Cookie header
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Accept": "*/*",
        }

        try:
            r = await client.get(self.ME_URL, headers=headers)

            if r.status_code == 200:
                try:
                    data = r.json()
                    if data.get("id"):
                        return {
                            "Status": "LIVE",
                            "Platform": "facebook",
                            "fb_id": data.get("id"),
                            "name": data.get("name", ""),
                            "email": data.get("email", ""),
                            "Reason": "",
                        }
                except Exception:
                    pass
                return {
                    "Status": "UNKNOWN",
                    "Platform": "facebook",
                    "Reason": "non-json response",
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
        """Facebook par post publish karo."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Content-Type": "application/json",
        }

        payload = {"message": text}

        try:
            r = await client.post(self.FEED_URL, headers=headers, json=payload)

            if r.status_code == 200:
                try:
                    data = r.json()
                    post_id = data.get("id")
                    if post_id:
                        return True, post_id, None
                except Exception:
                    pass
                return True, None, None

            if r.status_code == 429:
                return False, None, "RATE"

            if r.status_code in (401, 403):
                return False, None, "DEAD"

            return False, None, f"HTTP {r.status_code}"

        except httpx.ConnectError as e:
            return False, None, "NET"
        except Exception as e:
            logger.exception("fb post")
            return False, None, str(e)[:80]
