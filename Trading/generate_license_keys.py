"""
Key-pair generator — run ONCE to create your signing keys.

    python generate_license_keys.py

Output
------
  private_key.pem   — keep this SECRET, never commit it
  public_key_b64.txt — paste the content into license_manager.py (_PUBLIC_KEY_B64)

The script also prints the base64 string directly to the console.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption
)
import base64
from pathlib import Path

private_key = Ed25519PrivateKey.generate()

# ── Save private key (PEM, unencrypted) ──────────────────────────────────────
priv_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
Path("private_key.pem").write_bytes(priv_pem)
print("private_key.pem  written — KEEP THIS SECRET, do NOT commit it.")

# ── Encode public key (DER → base64) ─────────────────────────────────────────
pub_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
pub_b64 = base64.b64encode(pub_der).decode()
Path("public_key_b64.txt").write_text(pub_b64)

print("\nPublic key (paste into tradinglib/license_manager.py → _PUBLIC_KEY_B64):")
print(pub_b64)
print("\npublic_key_b64.txt  written.")
