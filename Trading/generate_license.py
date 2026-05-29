"""
License generator — run on your developer machine to create signed license files.

    python generate_license.py --user max@example.com --tier strategy --expires 2027-12-31
    python generate_license.py --user max@example.com --tier paper    --expires 2027-12-31
    python generate_license.py --user max@example.com --tier full     --expires 2027-12-31

Arguments
---------
  --user      Licensee identifier (email or name)
  --tier      One of: strategy, paper, full
              strategy → strategy_engine only
              paper    → paper_trading only
              full     → strategy_engine + paper_trading
  --expires   ISO date YYYY-MM-DD (optional; omit for perpetual)
  --out       Output path (default: license.json)
  --key       Path to private_key.pem (default: private_key.pem)

The resulting license.json must be placed next to the Trading/ directory root
on the end-user's machine (same folder as asset_analyzer.py).
"""

import argparse
import base64
import json
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key

TIER_FEATURES = {
    "strategy": ["strategy_engine"],
    "paper":    ["paper_trading"],
    "full":     ["strategy_engine", "paper_trading"],
}

TIER_LABELS = {
    "strategy": "Strategy Engine",
    "paper":    "Paper Trading",
    "full":     "Full",
}


def main():
    parser = argparse.ArgumentParser(description="Generate a signed license file.")
    parser.add_argument("--user",    required=True,  help="Licensee email or name")
    parser.add_argument("--tier",    required=True,  choices=TIER_FEATURES.keys())
    parser.add_argument("--expires", default=None,   help="Expiry date YYYY-MM-DD (omit = perpetual)")
    parser.add_argument("--out",     default="license.json")
    parser.add_argument("--key",     default="private_key.pem")
    args = parser.parse_args()

    key_path = Path(args.key)
    if not key_path.exists():
        print(f"ERROR: Private key not found: {key_path}")
        print("Run  python generate_license_keys.py  first.")
        raise SystemExit(1)

    # Validate expiry date
    if args.expires:
        try:
            datetime.strptime(args.expires, "%Y-%m-%d")
        except ValueError:
            print("ERROR: --expires must be in YYYY-MM-DD format.")
            raise SystemExit(1)

    # Load private key
    priv_key = load_pem_private_key(key_path.read_bytes(), password=None)

    # Build payload
    payload: dict = {
        "user":     args.user,
        "tier":     TIER_LABELS[args.tier],
        "features": TIER_FEATURES[args.tier],
        "issued":   datetime.today().strftime("%Y-%m-%d"),
    }
    if args.expires:
        payload["expires"] = args.expires

    # Sign payload (deterministic JSON serialisation)
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = priv_key.sign(payload_bytes)
    sig_b64 = base64.b64encode(signature).decode()

    license_data = {"payload": payload, "signature": sig_b64}
    out_path = Path(args.out)
    out_path.write_text(json.dumps(license_data, indent=2), encoding="utf-8")

    print(f"License written to {out_path}")
    print(f"  User   : {payload['user']}")
    print(f"  Tier   : {payload['tier']}")
    print(f"  Features: {payload['features']}")
    print(f"  Issued : {payload['issued']}")
    print(f"  Expires: {payload.get('expires', 'never')}")


if __name__ == "__main__":
    main()
