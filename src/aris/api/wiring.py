"""Turn ``ApiSettings`` into the live objects a BankBot API process needs: the
keyring of publishers it trusts and the risk_id scheme it derives under.

Kept apart from ``create_app`` so tests can hand ``create_app`` objects
directly, while ``python -m aris.api`` builds them from the environment.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from aris.api.config import ApiSettings
from aris.attestation import PublisherKeyring, public_key_from_hex
from aris.risk_id import (
    HmacRiskIdDeriver,
    HsmRiskIdDeriver,
    OprfRiskIdDeriver,
    RiskIdDeriver,
)

if TYPE_CHECKING:
    import httpx


def build_keyring(settings: ApiSettings) -> PublisherKeyring:
    keyring = PublisherKeyring()
    for bank_id, hex_key in settings.trusted_publishers.items():
        keyring.register(bank_id, public_key_from_hex(hex_key))
    return keyring


def build_risk_ids(
    settings: ApiSettings,
    resources: ExitStack,
    *,
    oprf_http: httpx.Client | None = None,
) -> RiskIdDeriver:
    """``resources`` owns anything that must stay open for the process's
    lifetime (an HSM session, an HTTP connection pool). ``oprf_http`` lets a
    caller supply the transport to the authority (tests use an in-process
    one); by default the client opens its own."""
    if settings.risk_id_mode == "hmac":
        return HmacRiskIdDeriver()

    if settings.risk_id_mode == "oprf":
        from aris.oprf_service import HttpOprfAuthorityClient

        if not (settings.oprf_url and settings.oprf_bank_key and settings.oprf_fingerprint):
            raise ValueError("risk_id_mode=oprf needs an authority URL, bank key and fingerprint")
        client = HttpOprfAuthorityClient(
            settings.oprf_url,
            settings.bank_id,
            settings.oprf_bank_key,
            settings.oprf_fingerprint,
            http=oprf_http,
        )
        resources.callback(client.close)
        # Fetch and check the pinned key now: a wrong or unreachable authority
        # should stop the process at startup, not surface as a step-up on
        # every transfer once traffic arrives.
        _ = client.public_numbers
        return OprfRiskIdDeriver(client, settings.bank_id)

    from aris.hsm import DEFAULT_KEY_LABEL, get_consortium_key, open_hsm_session

    session = resources.enter_context(open_hsm_session())
    key = get_consortium_key(session, settings.hsm_key_label or DEFAULT_KEY_LABEL)
    return HsmRiskIdDeriver(key)
