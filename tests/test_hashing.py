from __future__ import annotations

import hashlib

import pytest

from aris.hashing import (
    MIN_KEY_BYTES,
    SaltNotConfigured,
    _length_prefixed_material,
    generate_key,
    load_salt,
    risk_id_for_account,
)

KEY = bytes.fromhex("a1b2c3d4" * 8)  # 32 bytes
IFSC = "HDFC0001234"
OTHER_IFSC = "ICIC0009876"


def test_same_pair_yields_same_risk_id_at_every_bank():
    assert risk_id_for_account(IFSC, "ACC-999", KEY) == risk_id_for_account(IFSC, "ACC-999", KEY)
    assert len(risk_id_for_account(IFSC, "ACC-999", KEY)) == 64


def test_different_accounts_differ():
    assert risk_id_for_account(IFSC, "ACC-999", KEY) != risk_id_for_account(IFSC, "ACC-998", KEY)


def test_different_ifsc_differ():
    """The whole point of SS3.3: the same account number at two different banks
    must not collide onto the same risk_id."""
    assert risk_id_for_account(IFSC, "ACC-999", KEY) != risk_id_for_account(
        OTHER_IFSC, "ACC-999", KEY
    )


def test_different_keys_yield_different_ids():
    other = bytes.fromhex("f0e1d2c3" * 8)
    assert risk_id_for_account(IFSC, "ACC-999", KEY) != risk_id_for_account(IFSC, "ACC-999", other)


def test_is_hmac_not_plain_suffix_hash():
    """Guards the primitive itself, not just its output shape."""
    legacy = hashlib.sha256(b"ACC-999" + KEY).hexdigest()
    assert risk_id_for_account(IFSC, "ACC-999", KEY) != legacy


def test_normalization_folds_case_and_whitespace():
    assert risk_id_for_account(" hdfc0001234 ", " acc-999 ", KEY) == risk_id_for_account(
        IFSC, "ACC-999", KEY
    )


@pytest.mark.parametrize(
    "bad", ["", "   ", "AC", "ACC_999", "ACC/999", "A" * 40, "ACC-999\x00", "ACC;999"]
)
def test_malformed_accounts_are_rejected(bad: str):
    with pytest.raises((ValueError, TypeError)):
        risk_id_for_account(IFSC, bad, KEY)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "HDFC000123",  # one char short
        "HDFC00012345",  # one char long
        "hdfc1001234",  # 5th character must be the literal '0'
        "1DFC0001234",  # bank code must be letters
        "HDFC0001234\x00",
    ],
)
def test_malformed_ifsc_is_rejected(bad: str):
    with pytest.raises((ValueError, TypeError)):
        risk_id_for_account(bad, "ACC-999", KEY)


def test_ifsc_normalization_folds_case_and_whitespace():
    assert risk_id_for_account(" hdfc0001234 ", "ACC-999", KEY) == risk_id_for_account(
        "HDFC0001234", "ACC-999", KEY
    )


def test_surrounding_whitespace_is_formatting_noise():
    """A trailing newline is display formatting, so it folds rather than errors."""
    assert risk_id_for_account(IFSC, "ACC-999\n", KEY) == risk_id_for_account(IFSC, "ACC-999", KEY)


def test_separators_stay_significant():
    """Folding '-' away would merge accounts a bank may treat as distinct."""
    assert risk_id_for_account(IFSC, "ACC-999", KEY) != risk_id_for_account(IFSC, "ACC999", KEY)


def test_non_string_account_rejected():
    with pytest.raises(TypeError):
        risk_id_for_account(IFSC, 12345, KEY)  # type: ignore[arg-type]


def test_short_key_rejected():
    with pytest.raises(ValueError):
        risk_id_for_account(IFSC, "ACC-999", b"short")


def test_load_salt_fails_closed_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARIS_SALT", raising=False)
    monkeypatch.delenv("ARIS_DEV_MODE", raising=False)
    with pytest.raises(SaltNotConfigured):
        load_salt()


@pytest.mark.parametrize(
    "weak",
    [
        "aa" * (MIN_KEY_BYTES - 1),  # correctly encoded but too short
        "consortium-key-passphrase",  # a passphrase, not random bytes
        "hunter2",
        "",
    ],
)
def test_load_salt_rejects_weak_key(monkeypatch: pytest.MonkeyPatch, weak: str):
    """Length alone is not entropy: a passphrase is recoverable from one
    known (account, risk_id) pair, which every member bank has."""
    monkeypatch.setenv("ARIS_SALT", weak)
    with pytest.raises(SaltNotConfigured):
        load_salt()


def test_generated_key_is_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARIS_SALT", generate_key())
    assert len(load_salt()) >= MIN_KEY_BYTES


def test_dev_key_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARIS_SALT", raising=False)
    monkeypatch.setenv("ARIS_DEV_MODE", "1")
    assert isinstance(load_salt(), bytes)


@pytest.mark.parametrize(
    "crafted",
    [
        chr(0x0131) + "2345678",  # DOTLESS I
        chr(0x00DF) + "2345678",  # SHARP S
        chr(0xFB00) + "2345678",  # LATIN SMALL LIGATURE FF
        chr(0x017F) + "2345678",  # LATIN SMALL LETTER LONG S
    ],
)
def test_unicode_case_folding_cannot_forge_a_collision(crafted: str):
    """str.upper() maps 'i' -> 'I' and 'ss' -> 'SS', so folding before the
    ASCII check would let crafted input collide with a real account."""
    with pytest.raises(ValueError):
        risk_id_for_account(IFSC, crafted, KEY)


def test_length_prefixed_material_prevents_concatenation_collision():
    """The exact collision docs/SECURITY.md SS3.3 calls out: bare concatenation
    of ("HDFC0001234", "5678") and ("HDFC000123", "45678") is identical bytes.
    Tested directly against the encoding helper -- not through
    risk_id_for_account's public IFSC validation, which happens to reject the
    10-character variant on format grounds alone and would let this pass
    trivially for the wrong reason.
    """
    assert _length_prefixed_material("HDFC0001234", "5678") != _length_prefixed_material(
        "HDFC000123", "45678"
    )
