"""Tests for proxy parsing module."""

import pytest
from src.utils.proxy import norm_proxy


def test_norm_proxy_http():
    """Test HTTP proxy parsing."""
    result = norm_proxy("http://user:pass@host:8080")
    assert result == "http://user:pass@host:8080"


def test_norm_proxy_socks5():
    """Test SOCKS5 proxy parsing."""
    result = norm_proxy("socks5://user:pass@1.2.3.4:1080")
    assert result == "socks5://user:pass@1.2.3.4:1080"


def test_norm_proxy_socks5h():
    """Test SOCKS5h proxy parsing."""
    result = norm_proxy("socks5h://user:pass@1.2.3.4:1080")
    assert result == "socks5h://user:pass@1.2.3.4:1080"


def test_norm_proxy_provider_format():
    """Test provider format host:port:user:pass."""
    result = norm_proxy("1.2.3.4:8080:user:pass")
    assert result == "http://user:pass@1.2.3.4:8080"


def test_norm_proxy_ipv6():
    """Test IPv6 host parsing."""
    result = norm_proxy("socks5://user:pass@[2001:db8::1]:1080")
    assert result == "socks5://user:pass@[2001:db8::1]:1080"


def test_norm_proxy_unbracketed_ipv6():
    """Test unbracketed IPv6 parsing."""
    result = norm_proxy("socks5://user:pass@2001:db8::1:1080")
    assert result == "socks5://user:pass@[2001:db8::1]:1080"


def test_norm_proxy_invalid():
    """Test invalid proxy entries."""
    assert norm_proxy("vless://uuid@host:443") is None
    assert norm_proxy("ss://uuid@host:443") is None
    assert norm_proxy("trojan://uuid@host:443") is None
    assert norm_proxy("vmess://uuid@host:443") is None


def test_norm_proxy_empty():
    """Test empty/invalid inputs."""
    assert norm_proxy("") is None
    assert norm_proxy("# comment") is None
    assert norm_proxy("") is None
    assert norm_proxy("not-a-proxy") is None


def test_norm_proxy_rejects_unsupported_and_malformed():
    """Unknown schemes / empty hosts must not silently become valid proxies."""
    assert norm_proxy("socks4://1.2.3.4:1080") is None
    assert norm_proxy("socks4a://1.2.3.4:1080") is None
    assert norm_proxy("http://") is None
    assert norm_proxy("://bad") is None


def test_norm_proxy_bracketed_ipv6_without_auth():
    """Bracketed IPv6 must stay bracketed in the output URL."""
    assert norm_proxy("[2001:db8::1]:1080") == "http://[2001:db8::1]:1080"
    assert (
        norm_proxy("socks5://[2001:db8::1]:1080")
        == "socks5://[2001:db8::1]:1080"
    )


def test_norm_proxy_trailing_slash():
    assert norm_proxy("http://1.2.3.4:8080/") == "http://1.2.3.4:8080"


def test_mask_sensitive_hides_bare_credentials():
    from src.utils.proxy import mask_sensitive

    masked = mask_sensitive("connect user:supersecret@1.2.3.4:1080 failed")
    assert "supersecret" not in masked
    assert "***:***@" in masked