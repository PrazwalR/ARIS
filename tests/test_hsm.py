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
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Imports below must follow importorskip so a missing optional [hsm] extra
# skips this module instead of erroring at collection.
pkcs11 = pytest.importorskip("pkcs11", reason="pkcs11 not installed (optional [hsm] extra)")
from pkcs11 import Attribute, Mechanism  # noqa: E402

from aris.hashing import current_epoch  # noqa: E402
from aris.hsm import (  # noqa: E402
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
    # A second bank's token, provisioned up front: the PKCS#11 library
    # enumerates slots once per process, so a token created later is invisible.
    subprocess.run(
        [
            "softhsm2-util",
            "--init-token",
            "--free",
            "--label",
            f"{label}-b",
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


@requires_softhsm
class TestWiredThroughBankBotAndApi:
    """SS3.6 wired in: the HSM-resident key is what both publisher and BankBot
    derive under, and `python -m aris.api` can be configured to use it."""

    def test_flag_published_under_the_hsm_key_blocks(self, hsm_config, keyring, bank_b):
        from aris.bankbot import BankBot, TransferRequest
        from aris.bus import InMemoryRiskBus
        from aris.risk_id import HsmRiskIdDeriver, publish_id
        from aris.schema import Decision, RiskSignal

        now = datetime.now(timezone.utc)
        with open_hsm_session(**hsm_config) as session:
            generate_consortium_key(session, label="wired-key")
            ids = HsmRiskIdDeriver(get_consortium_key(session, label="wired-key"))
            bus = InMemoryRiskBus(keyring)
            bus.publish(
                bank_b.sign(
                    RiskSignal(
                        risk_id=publish_id(ids, "HDFC0001234", "ACC-999", now),
                        risk_score=95,
                        confidence=0.99,
                        reason_codes=("confirmed_fraud",),
                        model_version="v0.4-fl",
                        source_bank_id="BANK-B",
                    )
                )
            )
            decision = BankBot(bus, risk_ids=ids).pre_transaction(
                TransferRequest(
                    user_ref="anu",
                    bank_id="BANK-A",
                    receiver_ifsc="HDFC0001234",
                    receiver_account="ACC-999",
                    amount_minor=500_000,
                )
            )
        assert decision.decision is Decision.BLOCK

    def test_api_hsm_mode_opens_the_token_from_env(self, hsm_config, monkeypatch):
        from contextlib import ExitStack

        from aris.api.config import ApiSettings
        from aris.api.wiring import build_risk_ids
        from aris.risk_id import HsmRiskIdDeriver

        with open_hsm_session(**hsm_config) as session:
            generate_consortium_key(session, label="api-key")
        monkeypatch.setenv("ARIS_HSM_MODULE", hsm_config["module_path"])
        monkeypatch.setenv("ARIS_HSM_TOKEN_LABEL", hsm_config["token_label"])
        monkeypatch.setenv("ARIS_HSM_PIN", hsm_config["user_pin"])
        with ExitStack() as resources:
            settings = ApiSettings(risk_id_mode="hsm", hsm_key_label="api-key")
            ids = build_risk_ids(settings, resources)
            assert isinstance(ids, HsmRiskIdDeriver)
            first, previous = ids.candidates("HDFC0001234", "ACC-999", datetime.now(timezone.utc))
            assert first != previous  # current and previous epoch subkeys differ

    def test_api_hsm_mode_fails_closed_without_a_provisioned_key(self, hsm_config, monkeypatch):
        from contextlib import ExitStack

        from aris.api.config import ApiSettings
        from aris.api.wiring import build_risk_ids

        monkeypatch.setenv("ARIS_HSM_MODULE", hsm_config["module_path"])
        monkeypatch.setenv("ARIS_HSM_TOKEN_LABEL", hsm_config["token_label"])
        monkeypatch.setenv("ARIS_HSM_PIN", hsm_config["user_pin"])
        with ExitStack() as resources, pytest.raises(HsmNotConfigured):
            build_risk_ids(ApiSettings(risk_id_mode="hsm", hsm_key_label="absent"), resources)


@requires_softhsm
class TestConsortiumKeyIsSharedAcrossBanks:
    """Two banks' tokens must derive the same risk_ids, or every cross-bank
    lookup misses (and a miss resolves to allow)."""

    def _second_token(self, hsm_config):
        label = "aris-test-token-b"
        subprocess.run(
            [
                "softhsm2-util",
                "--init-token",
                "--free",
                "--label",
                label,
                "--pin",
                "1234",
                "--so-pin",
                "5678",
            ],
            check=True,
            capture_output=True,
        )
        return {**hsm_config, "token_label": label}

    def test_imported_key_matches_across_tokens_and_the_hmac_scheme(self, hsm_config):
        from aris.hashing import risk_id_for_account
        from aris.hsm import import_consortium_key

        raw = os.urandom(32)
        other = self._second_token(hsm_config)
        with open_hsm_session(**hsm_config) as a, open_hsm_session(**other) as b:
            ka = import_consortium_key(a, raw, label="shared")
            kb = import_consortium_key(b, raw, label="shared")
            assert ka[Attribute.EXTRACTABLE] is False
            assert ka[Attribute.SENSITIVE] is True
            id_a = risk_id_for_account_hsm("HDFC0001234", "999988887777", ka, epoch=20000)
            id_b = risk_id_for_account_hsm("HDFC0001234", "999988887777", kb, epoch=20000)
        assert id_a == id_b
        assert id_a == risk_id_for_account("HDFC0001234", "999988887777", raw, epoch=20000)

    def test_imported_key_cannot_be_read_back(self, hsm_config):
        from aris.hsm import import_consortium_key

        with open_hsm_session(**hsm_config) as s:
            key = import_consortium_key(s, os.urandom(32), label="no-readback")
            with pytest.raises(pkcs11.AttributeSensitive):
                key[Attribute.VALUE]

    def test_wrong_length_key_is_refused(self, hsm_config):
        from aris.hsm import import_consortium_key

        with open_hsm_session(**hsm_config) as s, pytest.raises(ValueError, match="32 bytes"):
            import_consortium_key(s, b"short", label="bad")

    def test_canary_checks_the_scheme_actually_in_use(self, hsm_config, keyring, bank_b):
        from aris.bus import InMemoryRiskBus
        from aris.canary import check_epoch_canary, publish_epoch_canary
        from aris.hsm import import_consortium_key
        from aris.risk_id import HsmRiskIdDeriver

        with open_hsm_session(**hsm_config) as s:
            ours = HsmRiskIdDeriver(import_consortium_key(s, os.urandom(32), label="canary-k"))
            drifted = HsmRiskIdDeriver(
                import_consortium_key(s, os.urandom(32), label="canary-drift")
            )
            bus = InMemoryRiskBus(keyring)
            publish_epoch_canary(bus, bank_b, risk_ids=ours)
            assert check_epoch_canary(bus, risk_ids=ours)
            assert not check_epoch_canary(bus, risk_ids=drifted)
