"""Instagram platform module.

Live check, post, and db helpers for Instagram accounts.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import BasePlatform

logger = logging.getLogger(__name__)


class InstagramPlatform(BasePlatform):
    NAME = "instagram"

    # Instagram private API endpoints
    CURRENT_USER_URL = "https://i.instagram.com/api/v1/accounts/current_user/?edit=true"
    MEDIA_CONFIGURE_URL = "https://i.instagram.com/api/v1/media/configure/"

    async def live_check(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Instagram cookie se check karo ki account working hai ya dead."""

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Instagram 269.0.0.19.107 Android (24/7.0; 480dpi; 1080x1920; samsung; SM-G973F; beyond1; qcom; en_US; 314675575)",
            "Cookie": cookie_header,
            "Accept": "*/*",
            "X-IG-App-ID": "936619743392459",
        }

        try:
            r = await client.get(self.CURRENT_USER_URL, headers=headers)

            if r.status_code == 200:
                try:
                    data = r.json()
                    user = data.get("user", {}) if isinstance(data, dict) else {}
                    if user.get("pk") or user.get("username"):
                        return {
                            "Status": "LIVE",
                            "Platform": "instagram",
                            "ig_id": str(user.get("pk", "")),
                            "username": user.get("username", ""),
                            "full_name": user.get("full_name", ""),
                            "Reason": "",
                        }
                except Exception:
                    pass
                return {
                    "Status": "UNKNOWN",
                    "Platform": "instagram",
                    "Reason": "non-json response",
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
            "User-Agent": "Instagram 269.0.0.19.107 Android (24/7.0; 480dpi; 1080x1920; samsung; SM-G973F; beyond1; qcom; en_US; 314675575)",
            "Cookie": cookie_header,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-IG-App-ID": "936619743392459",
        }

        payload = {"caption": text}

        try:
            r = await client.post(self.MEDIA_CONFIGURE_URL, headers=headers, data=payload)

            if r.status_code == 200:
                try:
                    data = r.json()
                    media_id = data.get("media", {}).get("pk") if isinstance(data, dict) else None
                    return True, str(media_id) if media_id else None, None
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
            logger.exception("ig post")
            return False, None, str(e)[:80]
