"""docs/SECURITY.md SS3.6: HSM-resident consortium key via PKCS#11.

Needs SoftHSM2 installed (`brew install softhsm` / `apt install softhsm2`) --
skips cleanly, not silently, when it isn't, same as the Kafka/mTLS suites do
for their infrastructure. This module provisions its own throwaway token (a
fresh temp directory + `softhsm2-util --init-token`) rather than depending on
one having been set up manually first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pkcs11
import pytest
from pkcs11 import Attribute, Mechanism

from aris.hashing import current_epoch
from aris.hsm import (
    HsmNotConfigured,
    generate_consortium_key,
    get_consortium_key,
    open_hsm_session,
    risk_id_for_account_hsm,
)

_MODULE_CANDIDATES = (
    "/opt/homebrew/lib/softhsm/libsofthsm2.so",
    "/usr/local/lib/softhsm/libsofthsm2.so",
    "/usr/lib/softhsm/libsofthsm2.so",
    "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
)


def _find_softhsm_module() -> str | None:
    for candidate in _MODULE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for path in Path("/opt/homebrew/Cellar/softhsm").glob("*/lib/softhsm/libsofthsm2.so"):
        return str(path)
    return None


def _softhsm_available() -> bool:
    return _find_softhsm_module() is not None and shutil.which("softhsm2-util") is not None


requires_softhsm = pytest.mark.skipif(
    not _softhsm_available(),
    reason="SoftHSM2 not installed -- `brew install softhsm` (or `apt install softhsm2`) "
    "to exercise this suite",
)


@pytest.fixture(scope="module")
def hsm_config():
    if not _softhsm_available():
        pytest.skip("SoftHSM2 not installed")

    module_path = _find_softhsm_module()
    tmpdir = Path(tempfile.mkdtemp(prefix="aris-softhsm-test-"))
    token_dir = tmpdir / "tokens"
    token_dir.mkdir()
    conf_path = tmpdir / "softhsm2.conf"
    conf_path.write_text(
        f"directories.tokendir = {token_dir}\nobjectstore.backend = file\nlog.level = INFO\n"
    )
    os.environ["SOFTHSM2_CONF"] = str(conf_path)

    label = "aris-test-token"
    pin = "1234"
    subprocess.run(
        [
            "softhsm2-util",
            "--init-token",
            "--slot",
            "0",
            "--label",
            label,
            "--pin",
            pin,
            "--so-pin",
            "5678",
        ],
        check=True,
        capture_output=True,
    )
    return {"module_path": module_path, "token_label": label, "user_pin": pin}


@requires_softhsm
class TestKeyNeverLeavesTheToken:
    def test_a_generated_key_is_marked_sensitive_and_non_extractable(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="k1")
            assert key[Attribute.EXTRACTABLE] is False
            assert key[Attribute.SENSITIVE] is True

    def test_reading_the_raw_key_value_is_refused(self, hsm_config):
        """The PKCS#11-level enforcement of SS3.6's actual requirement:
        not "the key is hard to get at", but that this process cannot read
        it back out at all, via any API this module exposes."""
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="k2")
            with pytest.raises(pkcs11.AttributeSensitive):
                key[Attribute.VALUE]

    def test_signing_still_works_without_ever_reading_the_key(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="k3")
            sig = key.sign(b"test material", mechanism=Mechanism.SHA256_HMAC)
            assert isinstance(sig, bytes)
            assert len(sig) == 32  # HMAC-SHA256


@requires_softhsm
class TestProvisioningIsSeparateFromRuntimeLookup:
    def test_get_consortium_key_fails_closed_when_not_provisioned(self, hsm_config):
        """Mirrors aris.hashing.load_salt: a missing key raises, and never
        silently derives one no other bank would agree on."""
        with open_hsm_session(**hsm_config) as session, pytest.raises(HsmNotConfigured):
            get_consortium_key(session, label="never-provisioned")

    def test_get_consortium_key_finds_a_provisioned_one(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            generate_consortium_key(session, label="findable-key")
            found = get_consortium_key(session, label="findable-key")
            assert found[Attribute.EXTRACTABLE] is False


@requires_softhsm
class TestSessionFailsClosed:
    def test_wrong_module_path_raises_hsm_not_configured(self):
        with (
            pytest.raises(HsmNotConfigured),
            open_hsm_session(module_path="/no/such/module.so", token_label="x", user_pin="1234"),
        ):
            pass

    def test_wrong_pin_raises_hsm_not_configured(self, hsm_config):
        with (
            pytest.raises(HsmNotConfigured),
            open_hsm_session(
                module_path=hsm_config["module_path"],
                token_label=hsm_config["token_label"],
                user_pin="wrong-pin",
            ),
        ):
            pass

    def test_env_var_fallback(self, hsm_config, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARIS_HSM_MODULE", hsm_config["module_path"])
        monkeypatch.setenv("ARIS_HSM_TOKEN_LABEL", hsm_config["token_label"])
        monkeypatch.setenv("ARIS_HSM_PIN", hsm_config["user_pin"])
        with open_hsm_session() as session:
            generate_consortium_key(session, label="env-var-key")

    def test_missing_env_vars_raise_hsm_not_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARIS_HSM_MODULE", raising=False)
        with pytest.raises(HsmNotConfigured), open_hsm_session():
            pass


@requires_softhsm
class TestRiskIdDerivation:
    def test_output_format_matches_the_hmac_derivation(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="fmt-key")
            risk_id = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key)
            assert len(risk_id) == 64
            int(risk_id, 16)  # must be valid hex

    def test_same_account_same_epoch_is_deterministic(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="det-key")
            epoch = current_epoch()
            a = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key, epoch=epoch)
            b = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key, epoch=epoch)
            assert a == b

    def test_different_accounts_differ(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="acct-key")
            a = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key)
            b = risk_id_for_account_hsm("HDFC0001234", "ACC-998", key)
            assert a != b

    def test_different_ifsc_differ(self, hsm_config):
        """Keeps SS3.3's fix: same account at two different banks must not
        collide onto the same id."""
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="ifsc-key")
            a = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key)
            b = risk_id_for_account_hsm("ICIC0009876", "ACC-999", key)
            assert a != b

    def test_different_epochs_differ(self, hsm_config):
        """Keeps SS3.2's fix: the epoch subkey (derived via one C_Sign call,
        not the root key itself) still rotates daily."""
        with open_hsm_session(**hsm_config) as session:
            key = generate_consortium_key(session, label="epoch-key")
            epoch = current_epoch()
            a = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key, epoch=epoch)
            b = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key, epoch=epoch + 1)
            assert a != b

    def test_a_different_root_key_yields_a_different_risk_id(self, hsm_config):
        with open_hsm_session(**hsm_config) as session:
            key1 = generate_consortium_key(session, label="root-key-1")
            key2 = generate_consortium_key(session, label="root-key-2")
            epoch = current_epoch()
            a = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key1, epoch=epoch)
            b = risk_id_for_account_hsm("HDFC0001234", "ACC-999", key2, epoch=epoch)
            assert a != b
