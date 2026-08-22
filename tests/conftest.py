from __future__ import annotations

import pytest

from aris.attestation import Publisher, PublisherKeyring

# 32 bytes of hex, matching what load_salt() now requires of a real key.
TEST_SALT_HEX = "4f2c8a1e" * 8
TEST_KEY = bytes.fromhex(TEST_SALT_HEX)


@pytest.fixture(autouse=True)
def _configured_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a real key so none of them exercise the dev fallback."""
    monkeypatch.setenv("ARIS_SALT", TEST_SALT_HEX)
    monkeypatch.delenv("ARIS_DEV_MODE", raising=False)


@pytest.fixture
def keyring() -> PublisherKeyring:
    return PublisherKeyring()


@pytest.fixture
def bank_b(keyring: PublisherKeyring) -> Publisher:
    publisher = Publisher.generate("BANK-B")
    keyring.register(publisher.bank_id, publisher.public_key)
    return publisher


@pytest.fixture
def bank_evil(keyring: PublisherKeyring) -> Publisher:
    """A registered member that behaves badly -- the realistic insider threat."""
    publisher = Publisher.generate("BANK-EVIL")
    keyring.register(publisher.bank_id, publisher.public_key)
    return publisher
