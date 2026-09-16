"""Live demo snippet: proves the consortium key can be USED but never READ
back out of a PKCS#11 token. Run with `brew install softhsm` done first."""

import os
import subprocess
import tempfile
from pathlib import Path

import pkcs11
from pkcs11 import Attribute, Mechanism

from aris.hsm import generate_consortium_key, open_hsm_session

# Locate the SoftHSM2 module (same search aris/hsm.py's tests use).
candidates = list(Path("/opt/homebrew/Cellar/softhsm").glob("*/lib/softhsm/libsofthsm2.so"))
module_path = str(candidates[0])

tmpdir = Path(tempfile.mkdtemp(prefix="aris-hsm-demo-"))
(tmpdir / "tokens").mkdir()
conf = tmpdir / "softhsm2.conf"
conf.write_text(f"directories.tokendir = {tmpdir / 'tokens'}\nobjectstore.backend = file\n")

os.environ["SOFTHSM2_CONF"] = str(conf)
subprocess.run(
    [
        "softhsm2-util",
        "--init-token",
        "--slot",
        "0",
        "--label",
        "review-demo",
        "--pin",
        "1234",
        "--so-pin",
        "5678",
    ],
    check=True,
    capture_output=True,
)

print("\n--- generating the consortium key INSIDE the PKCS#11 token ---")
with open_hsm_session(module_path=module_path, token_label="review-demo", user_pin="1234") as s:
    key = generate_consortium_key(s, label="demo-key")
    print(f"  CKA_EXTRACTABLE: {key[Attribute.EXTRACTABLE]}   (must be False)")
    print(f"  CKA_SENSITIVE:   {key[Attribute.SENSITIVE]}   (must be True)")

    print("\n--- using the key: HMAC-sign via C_Sign, key material never leaves the token ---")
    sig = key.sign(b"aris-epoch:20345", mechanism=Mechanism.SHA256_HMAC)
    print(f"  signature: {sig.hex()}")

    print("\n--- attempting to read the raw key value back out ---")
    try:
        raw = key[Attribute.VALUE]
        print(f"  !!! EXTRACTED: {raw.hex()}  <-- should never print this")
    except pkcs11.AttributeSensitive:
        print("  REFUSED by the token -- pkcs11.AttributeSensitive")
        print("  (this process can USE the key. It cannot READ it. Not even once.)")
