"""Facebook platform module.

Live check, post, and cookie rework for Facebook accounts.
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional, Tuple

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
    GRAPH_URL = "https://graph.facebook.com/v21.0"

    async def live_check_impl(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Facebook cookie se check karo ki account working hai ya dead."""

        # A valid FB session always has these two cookies. Missing them means
        # the cookie file is useless even before we hit the network.
        missing = [k for k in ("c_user", "xs") if not cookies.get(k)]
        if missing:
            return {
                "Status": "UNKNOWN",
                "Platform": "facebook",
                "Reason": (
                    "missing FB cookie(s): " + ",".join(missing)
                    + " (galat platform / incomplete export)"
                ),
                "NoRetry": True,
            }

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Cookie": cookie_header,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            r = await client.get(self.MOBILE_CHECK_URL, headers=headers)
            path = (r.url.path or "").lower()
            html = r.text or ""
            low = html.lower()

            if r.status_code == 200:
                # Explicit login page markers -> cookie dead.
                login_markers = (
                    "login_form",
                    'name="email"',
                    "log in to facebook",
                    "/login/?next",
                    "checkpoint",
                )
                login_hit = any(m in low for m in login_markers) or "login" in path
                # Logged-in markers -> cookie alive.
                live_markers = (
                    "/logout.php",
                    "mbasic_logout_button",
                    '"userid"',
                    "facebook.com/home.php",
                    "notifications",
                )
                live_hit = any(m in low for m in live_markers)

                if live_hit:
                    uname_match = re.search(r"<title>([^<]{1,80})</title>", html, re.I)
                    username = uname_match.group(1).strip() if uname_match else ""
                    username = re.sub(r"\s*[|\-]\s*Facebook\s*$", "", username, flags=re.I).strip()
                    if username.lower() in ("facebook", "log in", "login"):
                        username = ""

                    fb_id = ""
                    for pat in (
                        r'"userID"\s*:\s*"?(\d+)"?',
                        r'"USER_ID"\s*:\s*"?(\d+)"?',
                        r'"fbid"\s*:\s*"?(\d+)"?',
                        r"c_user=(\d+)",
                    ):
                        m = re.search(pat, html, re.I)
                        if m:
                            fb_id = m.group(1)
                            break

                    return {
                        "Status": "LIVE",
                        "Platform": "facebook",
                        "username": username,
                        "fb_id": fb_id,
                        "ig_id": "",
                        "Reason": "",
                    }

                if login_hit:
                    return {
                        "Status": "DEAD",
                        "Platform": "facebook",
                        "Reason": "login/checkpoint page",
                    }

                # 200 but neither marker: do not claim LIVE.
                return {
                    "Status": "UNKNOWN",
                    "Platform": "facebook",
                    "Reason": "HTTP 200 but no login/session marker",
                }

            if r.status_code in (302, 301):
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

    async def post(
        self,
        client: httpx.AsyncClient,
        cookies: Dict[str, str],
        text: str,
        media: Optional[str] = None,
        access_token: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Publish via Graph API when a token exists.

        Cookie-only posting needs fb_dtsg/CSRF form handling that breaks every
        few weeks; pretending a 200 from the homepage meant success produced
        false "posted" counts. Without a token we now return an explicit
        failure instead.
        """
        if not access_token:
            return (
                False,
                None,
                "UNSUPPORTED: cookie-only post nahi hai; DB me access_token add karo (Graph API)",
            )

        payload = {"message": text, "access_token": access_token}
        endpoint = f"{self.GRAPH_URL}/{target_id or 'me'}/feed"

        try:
            r = await client.post(endpoint, data=payload)
            if r.status_code == 200:
                data = r.json()
                post_id = data.get("id")
                if post_id:
                    return True, str(post_id), None
                return False, None, "graph: response me post id nahi"
            if r.status_code == 429:
                wait = rate_limiter.record_response("facebook", 429)
                if wait:
                    await asyncio.sleep(wait)
                return False, None, "RATE"
            if r.status_code in (401, 403):
                return False, None, "DEAD/token invalid"
            return False, None, f"HTTP {r.status_code}: {r.text[:80]}"
        except httpx.ConnectError:
            return False, None, "NET"
        except Exception as e:  # noqa: BLE001
            logger.exception("fb post")
            return False, None, str(e)[:80]