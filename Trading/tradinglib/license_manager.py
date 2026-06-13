"""
License manager — offline (Ed25519) + optional server validation.

Tiers / Features
----------------
FEATURE_FREE             always active, no license file needed
FEATURE_STRATEGY_ENGINE  requires a signed license.json with this feature
FEATURE_PAPER_TRADING    requires a signed license.json with this feature
FEATURE_SEASONALITY      requires a signed license.json with this feature

Usage
-----
    from tradinglib.license_manager import has_feature, FEATURE_STRATEGY_ENGINE

    if has_feature(FEATURE_STRATEGY_ENGINE):
        ...  # show strategy engine UI
"""

import json
import base64
import logging
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)

# ── Feature constants ────────────────────────────────────────────────────────

FEATURE_FREE = "free"
FEATURE_STRATEGY_ENGINE = "strategy_engine"
FEATURE_PAPER_TRADING = "paper_trading"
FEATURE_SEASONALITY = "seasonality"

ALL_FEATURES = [FEATURE_FREE, FEATURE_STRATEGY_ENGINE, FEATURE_PAPER_TRADING, FEATURE_SEASONALITY]

# ── Embedded public key ──────────────────────────────────────────────────────
# DER-encoded Ed25519 public key, base64-encoded.
# Replace this placeholder by running:  python generate_license_keys.py
# That script prints the PUBLIC_KEY_B64 value to paste here.
_PUBLIC_KEY_B64: str = "MCowBQYDK2VwAyEAziZUe33KbCHRH6owVUZkwscEgQwUuknKCzN5JaQC+iA="

# ── Optional server validation ───────────────────────────────────────────────
# Set to your validation endpoint URL to enable server checks.
# None  → offline-only mode (signature check is the sole guard).
# str   → signature is checked first; if valid, server is asked for confirmation.
LICENSE_SERVER_URL: str | None = None

# Behaviour when server is unreachable:
#   True  → treat as valid (fail-open, good for offline users)
#   False → treat as invalid (strict, requires connectivity)
SERVER_FAIL_OPEN: bool = True

# ── Internal state ───────────────────────────────────────────────────────────

_cached_features: set | None = None
_license_payload: dict | None = None
_license_error: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _license_path() -> Path:
    """Expected location: license.json next to the Trading/ folder root."""
    return Path(__file__).parent.parent / "license.json"


def _verify_signature(data: dict) -> dict:
    """
    Verifies the Ed25519 signature in *data* and returns the payload dict.
    Raises InvalidSignature or ValueError on any problem.
    """
    if _PUBLIC_KEY_B64.startswith("REPLACE_ME"):
        raise ValueError("Public key not configured — run generate_license_keys.py first.")

    payload = data.get("payload")
    sig_b64 = data.get("signature")
    if not payload or not sig_b64:
        raise ValueError("license.json is missing 'payload' or 'signature' fields.")

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = base64.b64decode(sig_b64)

    pub_key_der = base64.b64decode(_PUBLIC_KEY_B64)
    public_key = load_der_public_key(pub_key_der)
    public_key.verify(signature, payload_bytes)  # raises InvalidSignature if forged

    return payload


def _check_server(payload: dict) -> bool:
    """
    Asks the license server to confirm the payload.
    Returns True when the server says OK, or when the server is unreachable
    and SERVER_FAIL_OPEN is True.
    """
    if not LICENSE_SERVER_URL:
        return True
    try:
        import requests  # optional dependency for server mode
        resp = requests.post(
            LICENSE_SERVER_URL,
            json={"payload": payload},
            timeout=5,
        )
        return resp.status_code == 200 and resp.json().get("valid") is True
    except Exception as exc:
        logger.warning("License server unreachable (%s) — fail_open=%s", exc, SERVER_FAIL_OPEN)
        return SERVER_FAIL_OPEN


# ── Public API ───────────────────────────────────────────────────────────────

def load_license() -> set:
    """
    Reads and validates license.json.  Returns the set of active features.
    Always includes FEATURE_FREE.  Result is cached for the process lifetime.
    """
    global _cached_features, _license_payload, _license_error

    if _cached_features is not None:
        return _cached_features

    _cached_features = {FEATURE_FREE}
    path = _license_path()

    if not path.exists():
        return _cached_features

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        payload = _verify_signature(data)

        expires = payload.get("expires")
        if expires and date.fromisoformat(expires) < date.today():
            _license_error = f"License expired on {expires}."
            logger.warning(_license_error)
            return _cached_features

        if not _check_server(payload):
            _license_error = "License rejected by server."
            logger.warning(_license_error)
            return _cached_features

        features = set(payload.get("features", []))
        features.add(FEATURE_FREE)
        _cached_features = features
        _license_payload = payload
        logger.info("License OK — user=%s features=%s", payload.get("user"), features)

    except InvalidSignature:
        _license_error = "License signature is invalid."
        logger.error(_license_error)
    except Exception as exc:
        _license_error = f"License error: {exc}"
        logger.error(_license_error)

    return _cached_features


def has_feature(feature: str) -> bool:
    """Returns True when the loaded license includes *feature*."""
    return feature in load_license()


def get_license_info() -> dict:
    """
    Returns a dict with license metadata for display in the UI.
    Keys: tier, user, features, expires, error, path_exists
    """
    path = _license_path()
    load_license()  # ensure cache is warm

    if _license_payload:
        return {
            "tier": _license_payload.get("tier", "Custom"),
            "user": _license_payload.get("user", ""),
            "features": _license_payload.get("features", []),
            "expires": _license_payload.get("expires"),
            "error": _license_error,
            "path_exists": path.exists(),
        }

    return {
        "tier": "Free",
        "user": "",
        "features": [FEATURE_FREE],
        "expires": None,
        "error": _license_error,
        "path_exists": path.exists(),
    }


def reset_cache() -> None:
    """Clears the in-process cache so the license file is re-read on next call."""
    global _cached_features, _license_payload, _license_error
    _cached_features = None
    _license_payload = None
    _license_error = None
