from __future__ import annotations

import hashlib

import pytest

from aris.oprf import (
    OprfAuthority,
    OprfQueryRecord,
    OprfVerificationError,
    RateLimitExceeded,
    _mgf1,
    blind,
    full_domain_hash,
    risk_id_for_account_oprf,
    unblind,
    verify,
)

IFSC = "HDFC0001234"
OTHER_IFSC = "ICIC0009876"


@pytest.fixture(scope="module")
def authority() -> OprfAuthority:
    # Real RSA-2048 keygen (~40ms); shared across tests that don't need a
    # fresh key, so the suite doesn't pay that cost dozens of times over.
    return OprfAuthority()


class TestMgf1:
    def test_output_length_matches_request(self):
        assert len(_mgf1(b"seed", 100, hashlib.sha256)) == 100
        assert len(_mgf1(b"seed", 1, hashlib.sha256)) == 1
        assert len(_mgf1(b"seed", 256, hashlib.sha256)) == 256

    def test_deterministic(self):
        assert _mgf1(b"seed", 64, hashlib.sha256) == _mgf1(b"seed", 64, hashlib.sha256)

    def test_different_seeds_differ(self):
        assert _mgf1(b"seed-a", 64, hashlib.sha256) != _mgf1(b"seed-b", 64, hashlib.sha256)

    def test_longer_output_extends_rather_than_repeats(self):
        short = _mgf1(b"seed", 32, hashlib.sha256)
        long = _mgf1(b"seed", 64, hashlib.sha256)
        assert long[:32] == short


class TestFullDomainHash:
    def test_deterministic(self):
        n = 2**2048 - 1
        assert full_domain_hash(b"ACC-999", n) == full_domain_hash(b"ACC-999", n)

    def test_in_range(self):
        n = (1 << 2048) - 159  # not prime, just a fixed 2048-bit-ish modulus for range testing
        value = full_domain_hash(b"ACC-999", n)
        assert 0 <= value < n

    def test_different_messages_differ(self):
        n = 2**2048 - 1
        assert full_domain_hash(b"ACC-999", n) != full_domain_hash(b"ACC-998", n)

    def test_uses_the_full_bit_range_not_just_a_short_digest(self):
        """A bare SHA-256 digest reduced mod n would never exceed 2^256 --
        far short of a 2048-bit modulus's range. FDH via MGF1 should not be
        so limited."""
        n = (1 << 2048) - 159
        # Sample many messages; at least one full-domain hash should exceed
        # 2^256, which a short-digest-mod-n scheme could never produce.
        assert any(full_domain_hash(f"msg-{i}".encode(), n) >= (1 << 256) for i in range(20))


class TestBlindUnblindPrimitives:
    def test_round_trips_through_direct_modexp(self, authority: OprfAuthority):
        """Validates the blind/unblind algebra directly, independent of
        OprfAuthority.evaluate: blinding then applying the private exponent
        then unblinding must equal applying the private exponent directly."""
        n, e = authority.public_numbers
        d = authority._d  # test-only access to the private exponent
        m = full_domain_hash(b"ACC-999", n)

        r, blinded = blind(m, n, e)
        raw_result = pow(blinded, d, n)
        unblinded = unblind(raw_result, r, n)

        assert unblinded == pow(m, d, n)

    def test_blinding_actually_changes_the_value(self, authority: OprfAuthority):
        n, e = authority.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        _, blinded = blind(m, n, e)
        assert blinded != m

    def test_repeated_blinding_of_the_same_message_looks_unlinkable(self, authority: OprfAuthority):
        """Sanity check on the blinding property that gives the authority no
        way to correlate repeated queries for the same account: many
        independent blindings of one message must not collide or cluster."""
        n, e = authority.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        blinded_values = {blind(m, n, e)[1] for _ in range(50)}
        assert len(blinded_values) == 50

    def test_verify_accepts_a_genuine_signature_and_rejects_a_wrong_one(
        self, authority: OprfAuthority
    ):
        n, e = authority.public_numbers
        d = authority._d
        m = full_domain_hash(b"ACC-999", n)
        genuine = pow(m, d, n)
        assert verify(genuine, m, n, e) is True
        assert verify(genuine, m + 1, n, e) is False


class TestCannotBeComputedOffline:
    def test_the_public_exponent_does_not_produce_the_authoritys_output(
        self, authority: OprfAuthority
    ):
        """The RSA hardness assumption this whole construction rests on: only
        the private exponent d (held solely by the authority) can compute
        m^d mod n. A bank holding only the public (n, e) -- which every bank
        does, openly -- cannot derive it by itself, including by trying the
        one exponent it does have."""
        n, e = authority.public_numbers
        d = authority._d
        m = full_domain_hash(b"ACC-999", n)
        assert pow(m, e, n) != pow(m, d, n)


class TestEndToEndDerivation:
    def test_same_account_yields_the_same_risk_id_across_separate_sessions(
        self, authority: OprfAuthority
    ):
        """The whole point: two independent derivations (fresh random
        blinding factor each time, simulating two different banks or two
        separate queries) for the same account converge on the same id."""
        first = risk_id_for_account_oprf(IFSC, "ACC-999", authority, bank_id="BANK-A")
        second = risk_id_for_account_oprf(IFSC, "ACC-999", authority, bank_id="BANK-B")
        assert first == second
        assert len(first) == 64

    def test_different_accounts_differ(self, authority: OprfAuthority):
        a = risk_id_for_account_oprf(IFSC, "ACC-999", authority, bank_id="BANK-A")
        b = risk_id_for_account_oprf(IFSC, "ACC-998", authority, bank_id="BANK-A")
        assert a != b

    def test_different_ifsc_differ(self, authority: OprfAuthority):
        """Keeps SS3.3's fix: same account number at two different banks must
        not collide onto the same id."""
        a = risk_id_for_account_oprf(IFSC, "ACC-999", authority, bank_id="BANK-A")
        b = risk_id_for_account_oprf(OTHER_IFSC, "ACC-999", authority, bank_id="BANK-A")
        assert a != b

    def test_a_different_authority_key_yields_a_different_risk_id(self):
        auth1 = OprfAuthority()
        auth2 = OprfAuthority()
        a = risk_id_for_account_oprf(IFSC, "ACC-999", auth1, bank_id="BANK-A")
        b = risk_id_for_account_oprf(IFSC, "ACC-999", auth2, bank_id="BANK-A")
        assert a != b

    def test_a_misbehaving_authority_is_caught_by_verification(self, authority: OprfAuthority):
        """If the authority (or a network attacker) returns a response that
        does not correspond to the key it advertises, the client must not
        silently accept a wrong id -- unlike a base-mode DH-OPRF, this
        construction's output is self-verifiable without a separate proof
        protocol."""

        class _MisbehavingAuthority:
            @property
            def public_numbers(self) -> tuple[int, int]:
                return authority.public_numbers

            def evaluate(self, bank_id: str, blinded: int) -> int:
                real = authority.evaluate(bank_id, blinded)
                return (real + 1) % authority.public_numbers[0]

        with pytest.raises(OprfVerificationError):
            risk_id_for_account_oprf(IFSC, "ACC-999", _MisbehavingAuthority(), bank_id="BANK-A")


class TestRateLimiting:
    def test_exceeding_the_limit_is_rejected(self):
        auth = OprfAuthority(rate_limit=3)
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        for _ in range(3):
            _, blinded = blind(m, n, e)
            auth.evaluate("BANK-A", blinded)
        _, blinded = blind(m, n, e)
        with pytest.raises(RateLimitExceeded):
            auth.evaluate("BANK-A", blinded)

    def test_the_window_resets_over_time(self):
        clock = {"t": 0.0}
        auth = OprfAuthority(rate_limit=2, rate_window_s=10.0, now=lambda: clock["t"])
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)

        for _ in range(2):
            _, blinded = blind(m, n, e)
            auth.evaluate("BANK-A", blinded)
        _, blinded = blind(m, n, e)
        with pytest.raises(RateLimitExceeded):
            auth.evaluate("BANK-A", blinded)

        clock["t"] = 11.0  # past the 10s window
        _, blinded = blind(m, n, e)
        auth.evaluate("BANK-A", blinded)  # does not raise

    def test_limits_are_independent_per_bank(self):
        auth = OprfAuthority(rate_limit=1)
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)

        _, blinded_a = blind(m, n, e)
        auth.evaluate("BANK-A", blinded_a)
        _, blinded_a2 = blind(m, n, e)
        with pytest.raises(RateLimitExceeded):
            auth.evaluate("BANK-A", blinded_a2)

        _, blinded_b = blind(m, n, e)
        auth.evaluate("BANK-B", blinded_b)  # does not raise -- separate budget

    def test_zero_or_negative_rate_limit_rejected(self):
        with pytest.raises(ValueError):
            OprfAuthority(rate_limit=0)


class TestAuditLog:
    def test_records_who_and_when_but_not_what(self):
        auth = OprfAuthority()
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        _, blinded = blind(m, n, e)
        auth.evaluate("BANK-A", blinded)

        assert len(auth.audit_log) == 1
        record = auth.audit_log[0]
        assert isinstance(record, OprfQueryRecord)
        assert record.bank_id == "BANK-A"
        assert isinstance(record.at, float)

    def test_a_rejected_out_of_range_query_is_not_logged(self):
        auth = OprfAuthority()
        n, _ = auth.public_numbers
        with pytest.raises(ValueError):
            auth.evaluate("BANK-A", n)  # == n is out of [0, n)
        assert len(auth.audit_log) == 0

    def test_a_rate_limited_query_is_not_logged_twice(self):
        auth = OprfAuthority(rate_limit=1)
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        _, blinded = blind(m, n, e)
        auth.evaluate("BANK-A", blinded)
        _, blinded2 = blind(m, n, e)
        with pytest.raises(RateLimitExceeded):
            auth.evaluate("BANK-A", blinded2)
        assert len(auth.audit_log) == 1

    def test_log_is_bounded_and_drops_the_oldest(self):
        auth = OprfAuthority(rate_limit=10_000, audit_log_capacity=5)
        n, e = auth.public_numbers
        m = full_domain_hash(b"ACC-999", n)
        for _ in range(8):
            _, blinded = blind(m, n, e)
            auth.evaluate("BANK-A", blinded)
        assert len(auth.audit_log) == 5

    def test_zero_or_negative_audit_log_capacity_rejected(self):
        with pytest.raises(ValueError):
            OprfAuthority(audit_log_capacity=0)
