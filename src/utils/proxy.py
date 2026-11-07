"""Proxy utilities for Social Markhors Bot.

Same logic as evilmarkhors bot: PROXIES env, PROXY_TYPE, PROXY_FILE, /proxy add support.
"""

import asyncio
import logging
import os
import re
import time
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
PROXY_UNSUPPORTED = ("vless", "vmess", "trojan", "ss", "shadowsocks", "hysteria", "hysteria2", "tuic", "wireguard", "wg", "ssh", "ftp")

PROXIES: List[str] = []
PROXY_SKIPPED: List[str] = []
PROXY_HINT: set = set()
RUNTIME_PROXY_FILE = "proxies.txt"
PROXY_DISABLED = False


def check_socksio() -> bool:
    """Check if socksio is installed for SOCKS5 proxy support."""
    try:
        import socksio
        return True
    except ImportError:
        return False


def check_proxy_readiness() -> List[str]:
    """Check proxy configuration and return list of warnings."""
    warnings = []

    if PROXIES:
        socks_proxies = [p for p in PROXIES if p.startswith("socks5")]
        if socks_proxies and not check_socksio():
            warnings.append(
                "SOCKS5 proxies configured but 'socksio' not installed. "
                "Install: pip install socksio"
            )

    return warnings


def norm_proxy(raw: str, default_scheme: Optional[str] = None) -> Optional[str]:
    """Proxy line ko httpx-compatible URL mein convert karo."""
    s = (raw or "").strip().strip('"').strip("'").strip()
    if not s or s.startswith("#"):
        return None

    low = s.lower()
    for bad in PROXY_UNSUPPORTED:
        if low.startswith(bad + "://"):
            logger.warning("proxy skip: %s:// httpx support nahi karta", bad)
            PROXY_SKIPPED.append(bad)
            return None

    scheme, body = None, s
    m = re.match(r"^(socks5h|socks5|socks|https|http)\s*(?::\/\/|\||:)\s*(.*)$", s, re.I)
    if m and m.group(2):
        scheme = m.group(1).lower()
        if scheme == "socks":
            scheme = "socks5"
        body = m.group(2)

    if scheme is None:
        scheme = (default_scheme or os.getenv("PROXY_TYPE", "http")).lower()
        if scheme not in PROXY_SCHEMES:
            scheme = "http"

    if "@" in body:
        norm = _norm_auth_host(body)
        if not norm:
            logging.warning("proxy invalid: %s", s)
            return None
        auth, hostpart = norm
        s = scheme + "://" + auth + "@" + hostpart
    elif "/" in body:
        s = scheme + "://" + body
    else:
        parts = _colon_proxy_parts(body)
        if not parts:
            logging.warning("proxy invalid (host:port chahiye): %s", s)
            return None
        host, port, user, pwd = parts
        auth = ""
        if user or pwd:
            auth = user + ":" + pwd + "@"
        s = scheme + "://" + auth + host + ":" + port

    try:
        p = httpx.URL(s)
    except Exception:
        logging.warning("proxy invalid: %s", s)
        return None

    if p.scheme not in PROXY_SCHEMES or not p.host:
        logging.warning("proxy skip: %s", s)
        return None

    return str(p)


def _norm_auth_host(body: str) -> Optional[Tuple[str, str]]:
    """auth@host:port parse karo, unbracketed IPv6 ko bracket karo."""
    if "@" not in body:
        return None
    auth, hostpart = body.rsplit("@", 1)
    if not auth or not hostpart:
        return None
    if hostpart.startswith("["):
        return auth, hostpart
    if hostpart.count(":") <= 1:
        return auth, hostpart
    # Unbracketed IPv6 + port: last numeric segment port hai
    bits = hostpart.split(":")
    for i in range(len(bits) - 1, 0, -1):
        if bits[i].isdigit():
            if i + 1 != len(bits):
                return None
            host = ":".join(bits[:i])
            if not host:
                return None
            return auth, f"[{host}]:{bits[i]}"
    return None


def _colon_proxy_parts(s: str) -> Optional[Tuple[str, str, str, str]]:
    """host:port[:user[:pass]] parse karo (IPv4 / hostname / IPv6)."""
    s = (s or "").strip()
    if not s:
        return None

    if s.startswith("["):
        m = re.match(r"^\[([^\]]+)\]:(.*)$", s)
        if not m:
            return None
        host, rest = m.group(1), m.group(2).split(":")
        if not rest or not rest[0].isdigit():
            return None
        port, creds = rest[0], rest[1:]
    else:
        bits = s.split(":")
        if len(bits) in (2, 3, 4) and bits[1].isdigit():
            # host:port[:user[:pass]]
            host, port, creds = bits[0], bits[1], bits[2:]
        else:
            # Unbracketed IPv6: ipv6:port[:user[:pass]]
            # Port = aakhri numeric segment; uske baad creds.
            idx = None
            for i in range(len(bits) - 1, 0, -1):
                if bits[i].isdigit():
                    idx = i
                    break
            if idx is None:
                return None
            host = ":".join(bits[:idx])
            port = bits[idx]
            creds = bits[idx + 1:]

    if not host or not port.isdigit() or not (0 < int(port) < 65536):
        return None
    return (
        host,
        port,
        creds[0] if len(creds) >= 1 else "",
        creds[1] if len(creds) >= 2 else "",
    )


def load_proxies() -> List[str]:
    """PROXIES env + PROXY_FILE + proxies.txt (runtime), dedupe."""
    global PROXIES
    raw = os.getenv("PROXIES", "")
    items = [x for x in re.split(r"[\s,]+", raw or "") if x]

    env_file = os.getenv("PROXY_FILE", "")
    for path in (env_file, RUNTIME_PROXY_FILE):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                items += fh.read().splitlines()
        except FileNotFoundError:
            if path != RUNTIME_PROXY_FILE:
                logging.warning("PROXY_FILE nahi mila: %s", path)
        except Exception:
            logging.exception("PROXY_FILE")

    out, seen = [], set()
    for it in items:
        u = norm_proxy(it)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    PROXIES = out
    return PROXIES


def proxy_status_text() -> str:
    if PROXY_DISABLED and PROXIES:
        return f"Proxy: OFF (/proxy on) | {len(PROXIES)} configured"
    if not PROXIES:
        return "Proxy: OFF (PROXIES khaali) — /proxy add host:port:user:pass"
    lines = [f"Proxy: ON ({len(PROXIES)})", "Traffic only | Telegram + Supabase direct"]
    lines += [f"{i + 1}. {mask_proxy(p)}" for i, p in enumerate(PROXIES[:40])]
    if len(PROXIES) > 40:
        lines.append(f"... +{len(PROXIES) - 40} more")
    return "\n".join(lines)


def mask_proxy(proxy_url: str) -> str:
    """Hide proxy credentials before showing them in chat/logs."""
    try:
        u = httpx.URL(proxy_url)
        if u.username or u.password:
            return str(u.copy_with(username="***", password="***"))
    except Exception:  # noqa: BLE001
        pass
    return mask_sensitive(proxy_url)


def get_active_proxies() -> List[str]:
    """Proxies that may actually be used right now (respects /proxy off)."""
    if PROXY_DISABLED:
        return []
    return list(PROXIES or load_proxies())


def set_proxy_enabled(enabled: bool) -> None:
    global PROXY_DISABLED
    PROXY_DISABLED = not enabled


def add_proxy(entry: str) -> Tuple[bool, str]:
    """Add a proxy at runtime and persist it in proxies.txt.

    Returns (ok, normalized_url_or_error).
    """
    url = norm_proxy(entry)
    if not url:
        return False, "invalid proxy format"
    if url in PROXIES:
        return True, url
    try:
        with open(RUNTIME_PROXY_FILE, "a", encoding="utf-8") as fh:
            fh.write(entry.strip() + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not persist proxy to %s: %s", RUNTIME_PROXY_FILE, e)
    PROXIES.append(url)
    return True, url


async def test_proxies(limit: int = 10) -> List[str]:
    """Quick reachability test; returns per-proxy result lines (masked)."""
    async def probe(url: str) -> str:
        started = time.time()
        try:
            async with httpx.AsyncClient(
                proxy=url, timeout=httpx.Timeout(8.0), trust_env=False
            ) as client:
                r = await client.get("https://api.ipify.org?format=json")
                ms = (time.time() - started) * 1000
                if r.status_code == 200:
                    return f"OK   {mask_proxy(url)} ({ms:.0f}ms)"
                return f"FAIL {mask_proxy(url)} (HTTP {r.status_code})"
        except Exception as e:  # noqa: BLE001
            return f"FAIL {mask_proxy(url)} ({type(e).__name__})"

    proxies = get_active_proxies()[:limit]
    if not proxies:
        return ["Koi proxy configured nahi."]
    return list(await asyncio.gather(*(probe(p) for p in proxies)))


def mask_sensitive(text: str) -> str:
    """Mask sensitive data (cookie values, proxy credentials) in log messages."""
    # Mask cookie values in URLs or strings
    text = re.sub(r'(cookie=|Cookie:)[^;&\s]+', r'\1***masked***', text, flags=re.I)
    # Mask proxy credentials (user:pass@host)
    text = re.sub(r'://[^:@\s]+:[^:@\s]+@', r'://***:***@', text)
    # Mask auth headers
    text = re.sub(r'(authorization|authorization:)[^;\s\r\n]+', r'\1***masked***', text, flags=re.I)
    return text
