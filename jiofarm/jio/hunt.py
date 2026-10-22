"""JioFarm — Google AI Pro link hunting endpoints."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import unquote

import requests

from jiofarm.jio.auth import UA

# ------------------------------------------------------------------- constants

AUTH_URL = "https://www.jio.com/api/jio-authenticate-service/authenticate/authJsonData"
NAVIGATE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/navigate/Z0241"
ACTIVATE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/activate/Z0241?source=JIO"
GOOGLE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/google-ai"
SUBMIT_URL = "https://www.jio.com/api/jio-ott-service/ott/submission/submit"
GOOGLE_PAGE = "https://www.jio.com/selfcare/googleai/?header=no&type=Z0241&source=JIO"

JIO_HUNT_ENDPOINTS = [
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/google-ai"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/google-lead"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/submit"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/activate/googleai"),
]

RECHARGE_PAGES = [
    "https://tiny.jio.com/loginrecharge",
    "https://tiny.jio.com/loginirecharge",
]

LINK_MARKERS = (
    "serviceactivation.google.com",
    "one.google.com/activate-plan",
    "one.google.com/promo",
    "partnerPromotionToken",
)

ACTIVATION_PATTERN = re.compile(
    r"https?://serviceactivation[.]google[.]com/subscription/new/"
    r"(?P<token>[A-Za-z0-9_-]{50,})(?P<padding>={0,2})",
    re.IGNORECASE,
)

ASSET_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".svg", ".css", ".js")


# ------------------------------------------------------------------- helpers


def find_google_links(text: str) -> list[str]:
    """Extract Google redeem URLs from a blob of text."""
    if not text:
        return []

    # unescape & unquote deep URL encoded strings
    cleaned = html.unescape(text)
    for _ in range(4):
        decoded = unquote(cleaned)
        if decoded == cleaned:
            break
        cleaned = decoded

    match = ACTIVATION_PATTERN.search(cleaned)
    if match:
        return ["https://serviceactivation.google.com/subscription/new/" + match.group("token") + match.group("padding")]

    seen: dict[str, None] = {}
    for url in re.findall(r'https?://[^\s"\'<>]+', cleaned):
        u = url.rstrip('\\\'"')
        low = u.lower()
        if low.endswith(ASSET_EXTS) or "myjiostatic.cdn.jio.com" in low:
            continue
        if any(marker in u for marker in LINK_MARKERS):
            seen[u] = None
    return list(seen)


def _hunt_z0241_flow(s: requests.Session) -> str | None:
    """Primary flow: sequence for Jio Gemini Pro offer (product code Z0241)."""
    dash_headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://www.jio.com/selfcare/dashboard/",
    }
    offer_headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Origin": "https://www.jio.com",
        "Referer": GOOGLE_PAGE,
    }

    try:
        # Step 1: auth sync
        s.get(AUTH_URL, headers=dash_headers, timeout=15)

        # Step 2: navigate to Z0241 module
        s.get(NAVIGATE_URL, headers=dash_headers, timeout=15)

        # Step 3: trigger activation for Z0241
        s.get(ACTIVATE_URL, headers=offer_headers, timeout=15)

        # Step 4: request google-ai redeem payload
        res = s.get(GOOGLE_URL, headers=offer_headers, timeout=15)
        if res.status_code == 200:
            found = find_google_links(res.text)
            if found:
                try:
                    s.get(SUBMIT_URL, headers=offer_headers, timeout=10)
                except Exception:
                    pass
                return found[0]

            try:
                data = res.json()
                for key in ("redirectionURL", "redirectUrl", "url"):
                    v = (
                        data.get(key)
                        or (
                            data.get("data", {}).get(key)
                            if isinstance(data.get("data"), dict)
                            else data.get(key)
                        )
                    )
                    if isinstance(v, str):
                        sub_found = find_google_links(v)
                        if sub_found:
                            try:
                                s.get(SUBMIT_URL, headers=offer_headers, timeout=10)
                            except Exception:
                                pass
                            return sub_found[0]
            except Exception:
                pass
    except Exception:
        pass

    return None


def _hunt_fallback(s: requests.Session) -> str | None:
    """Fallback: legacy endpoints scan and recharge landing pages."""
    hdr = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.jio.com",
        "Referer": "https://www.jio.com/selfcare/googleai/",
    }

    for method, url in JIO_HUNT_ENDPOINTS:
        try:
            res = (
                s.get(url, headers=hdr, timeout=15)
                if method == "GET"
                else s.post(url, headers=hdr, json={}, timeout=15)
            )
            if res.status_code != 200:
                continue

            direct = find_google_links(res.text)
            if direct:
                return direct[0]

            try:
                data = res.json()
                blob = json.dumps(data)
                for key in ("redirectionURL", "redirectUrl", "url"):
                    v = (
                        data.get(key)
                        or (
                            data.get("data", {}).get(key)
                            if isinstance(data.get("data"), dict)
                            else data.get(key)
                        )
                    )
                    if isinstance(v, str) and any(m in v for m in LINK_MARKERS):
                        return v
                direct = find_google_links(blob)
                if direct:
                    return direct[0]
            except Exception:
                pass
        except Exception:
            continue

    for page in RECHARGE_PAGES:
        try:
            res = s.get(page, headers={"User-Agent": UA}, timeout=15)
            if res.status_code == 200:
                found = find_google_links(res.text)
                if found:
                    return found[0]
        except Exception:
            continue

    return None


# ------------------------------------------------------------------- main


def hunt_link(s: requests.Session) -> str | None:
    """Hunt for a Google AI Pro redeem link across Jio's subscription APIs.

    Tries primary Z0241 activation flow first, then falls back to general scan.
    """
    link = _hunt_z0241_flow(s)
    if link:
        return link
    return _hunt_fallback(s)
