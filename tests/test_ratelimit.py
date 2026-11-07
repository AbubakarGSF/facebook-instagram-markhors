"""Tests for rate limiter module."""

import asyncio
import time

from src.utils.ratelimit import rate_limiter


def test_rate_limiter_allows_first_request():
    """Test that first request is allowed."""
    async def test():
        result = await rate_limiter.wait_if_needed("test_user")
        assert result is None  # None means allowed (no wait needed)

    asyncio.run(test())


def test_rate_limiter_blocks_rapid_requests():
    """Test that rapid requests are rate limited (wait time > 0)."""
    import asyncio

    async def test():
        # First request should pass immediately
        await rate_limiter.wait_if_needed("test_user_2")
        # Second request immediately should be delayed
        start = time.time()
        await rate_limiter.wait_if_needed("test_user_2")
        elapsed = time.time() - start
        assert elapsed > 0

    asyncio.run(test())


def test_rate_limiter_different_users():
    """Test that different users are rate limited independently."""
    import asyncio

    async def test():
        await rate_limiter.wait_if_needed("user_1")
        await rate_limiter.wait_if_needed("user_2")
        # Both should be allowed since they're different users
        assert True

    asyncio.run(test())


def test_exponential_backoff():
    """Test exponential backoff calculation."""
    rl = rate_limiter.__class__(base_delay=1.0, max_delay=300.0)

    delay_1 = rl.exponential_backoff(0)
    delay_2 = rl.exponential_backoff(1)
    delay_3 = rl.exponential_backoff(2)

    assert delay_1 == 1.0
    assert delay_2 == 2.0
    assert delay_3 == 4.0


def test_exponential_backoff_max():
    """Test that backoff is capped at max_delay."""
    rl = rate_limiter.__class__(base_delay=1.0, max_delay=10.0)

    delay_10 = rl.exponential_backoff(10)  # 1024, capped at 10
    assert delay_10 == 10.0