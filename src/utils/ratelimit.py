"""Rate limiter with exponential backoff and Retry-After header support."""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Per-proxy/IP rate limiter with exponential backoff."""

    def __init__(self, max_retries: int = 5, base_delay: float = 1.0, max_delay: float = 300.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._last_request: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait_if_needed(self, key: str = "default") -> None:
        """Wait if rate limit requires it."""
        async with self._lock:
            now = time.time()
            last = self._last_request.get(key, 0)
            elapsed = now - last
            if elapsed < self.base_delay:
                await asyncio.sleep(self.base_delay - elapsed)
            self._last_request[key] = time.time()

    def record_response(self, key: str, status_code: int, retry_after: Optional[int] = None) -> Optional[float]:
        """Record response and return wait time if rate limited.

        Returns seconds to wait, or None if no wait needed.
        """
        if status_code == 429:
            wait = retry_after if retry_after else self.base_delay * 2
            wait = min(wait, self.max_delay)
            logger.warning(f"Rate limited on {key}, waiting {wait}s")
            return wait
        return None

    def exponential_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        return delay


# Global rate limiter instance
rate_limiter = RateLimiter()
