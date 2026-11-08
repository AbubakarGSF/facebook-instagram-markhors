"""Instagram platform module.

Live check, post, and cookie rework for Instagram accounts.
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

from .base import BasePlatform
from src.utils.ratelimit import rate_limiter

logger = logging.getLogger(__name__)


class InstagramPlatform(BasePlatform):
    NAME = "instagram"

    # Instagram check endpoints
    CHECK_URL = "https://www.instagram.com/"
    MOBILE_CHECK_URL = "https://www.instagram.com/accounts/edit/"
    API_URL = "https://i.instagram.com/api/v1/accounts/current_user/?edit=true"
    GRAPH_URL = "https://graph.facebook.com/v21.0"

    async def live_check_impl(self, client: httpx.AsyncClient, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Instagram cookie se check karo ki account working hai ya dead."""

        if not cookies.get("sessionid"):
            return {
                "Status": "UNKNOWN",
                "Platform": "instagram",
                "Reason": (
                    "missing IG cookie: sessionid "
                    "(galat platform / incomplete export)"
                ),
                "NoRetry": True,
            }

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 320.0.0.0"
            ),
            "Cookie": cookie_header,
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "129477",
            "X-IG-WWW-Claim": "0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            # Private mobile API: returns the logged-in user or login_required.
            r = await client.get(self.API_URL, headers=headers)
            path = (r.url.path or "").lower()

            if "login" in path:
                return {
                    "Status": "DEAD",
                    "Platform": "instagram",
                    "Reason": "login redirect",
                }

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:  # noqa: BLE001
                    data = {}
                user = data.get("user") if isinstance(data, dict) else None
                if user:
                    return {
                        "Status": "LIVE",
                        "Platform": "instagram",
                        "username": str(user.get("username") or ""),
                        "ig_id": str(
                            user.get("pk")
                            or user.get("pk_id")
                            or cookies.get("ds_user_id")
                            or ""
                        ),
                        "fb_id": "",
                        "followers": user.get("follower_count", ""),
                        "Reason": "",
                    }
                msg = ""
                if isinstance(data, dict):
                    msg = str(data.get("message", ""))
                low_msg = msg.lower()
                # Sirf explicit login/challenge response hi DEAD hai. Empty 200
                # aksar datacenter IP block hota hai — usay DEAD kehna false
                # positive tha.
                if any(k in low_msg for k in (
                    "login_required", "checkpoint_required",
                    "challenge_required", "user_required",
                    "please log in", "login required",
                )):
                    return {
                        "Status": "DEAD",
                        "Platform": "instagram",
                        "Reason": f"login_required ({msg})",
                    }
                return {
                    "Status": "UNKNOWN",
                    "Platform": "instagram",
                    "Reason": (
                        "HTTP 200 without user payload"
                        + (f" ({msg})" if msg else " (empty response / IP block?)")
                    ),
                }

            if r.status_code == 401:
                return {
                    "Status": "DEAD",
                    "Platform": "instagram",
                    "Reason": "auth failed (401)",
                }
            if r.status_code == 403:
                # Checkpoint / IP block ka proof nahi — false DEAD se bachao.
                return {
                    "Status": "UNKNOWN",
                    "Platform": "instagram",
                    "Reason": "HTTP 403 (IP/checkpoint block ho sakta hai)",
                }

            if r.status_code == 429:
                return {
                    "Status": "UNKNOWN",
                    "Platform": "instagram",
                    "Reason": "rate limited (429)",
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

    async def post(
        self,
        client: httpx.AsyncClient,
        cookies: Dict[str, str],
        text: str,
        media: Optional[str] = None,
        access_token: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Publish via Graph API only (Instagram has no text-only post).

        Requires an access token, the IG business account id and a publicly
        reachable image URL (`media`). Anything else fails explicitly instead
        of pretending a 200 meant a successful post.
        """
        if not access_token or not target_id:
            return (
                False,
                None,
                "UNSUPPORTED: IG post ke liye access_token + ig business id chahiye",
            )
        if not media or not str(media).lower().startswith(("http://", "https://")):
            return (
                False,
                None,
                "UNSUPPORTED: IG text-only post nahi hota; public image URL chahiye",
            )

        try:
            r = await client.post(
                f"{self.GRAPH_URL}/{target_id}/media",
                data={
                    "image_url": media,
                    "caption": text,
                    "access_token": access_token,
                },
            )
            if r.status_code != 200:
                if r.status_code == 429:
                    wait = rate_limiter.record_response("instagram", 429)
                    if wait:
                        await asyncio.sleep(min(wait, 60))
                    return False, None, "RATE"
                return False, None, f"HTTP {r.status_code}: {r.text[:80]}"
            try:
                creation_id = (r.json() or {}).get("id")
            except Exception:  # noqa: BLE001
                creation_id = None
            if not creation_id:
                return False, None, "graph: creation id nahi mila"

            # Graph image container ko process hone me waqt lagta hai. Direct
            # publish karne par "Media not ready" milta hai.
            status = ""
            try:
                polls = max(int(os.getenv("IG_MEDIA_POLLS", "10").strip() or 0), 0)
            except (TypeError, ValueError):
                logger.warning("Invalid IG_MEDIA_POLLS; default 10 use ho raha hai")
                polls = 10
            try:
                interval = max(
                    float(os.getenv("IG_MEDIA_POLL_SEC", "2").strip() or 0.5), 0.5
                )
            except (TypeError, ValueError):
                logger.warning("Invalid IG_MEDIA_POLL_SEC; default 2 use ho raha hai")
                interval = 2.0
            for _ in range(polls):
                try:
                    rs = await client.get(
                        f"{self.GRAPH_URL}/{creation_id}",
                        params={
                            "fields": "status_code",
                            "access_token": access_token,
                        },
                    )
                    if rs.status_code == 200:
                        status = str((rs.json() or {}).get("status_code") or "")
                except Exception:  # noqa: BLE001
                    logger.debug("IG media status poll failed", exc_info=True)
                if status in ("FINISHED", "ERROR", "EXPIRED"):
                    break
                await asyncio.sleep(interval)
            if status == "ERROR":
                return False, None, "graph: media processing error"
            if status == "EXPIRED":
                return False, None, "graph: media container expired"

            r2 = await client.post(
                f"{self.GRAPH_URL}/{target_id}/media_publish",
                data={"creation_id": creation_id, "access_token": access_token},
            )
            if r2.status_code == 200:
                post_id = (r2.json() or {}).get("id")
                if post_id:
                    return True, str(post_id), None
                return False, None, "graph: publish id nahi mila"
            if r2.status_code == 429:
                wait = rate_limiter.record_response("instagram", 429)
                if wait:
                    await asyncio.sleep(min(wait, 60))
                return False, None, "RATE"
            return False, None, f"publish HTTP {r2.status_code}: {r2.text[:80]}"
        except httpx.ConnectError:
            return False, None, "NET"
        except Exception as e:  # noqa: BLE001
            logger.exception("ig post")
            return False, None, str(e)[:80]