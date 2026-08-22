# ARIS security model

What the M0 implementation actually guarantees, what it does not, and what has to
change before any of this could face real money. Written to be defensible under
questioning: where a claim is weaker than the project write-up suggests, it says so.

## 1. Assets and adversaries

| Asset | Why it matters |
| --- | --- |
| Raw transaction rows | Never leave a bank. This is the premise of the whole design. |
| Customer account numbers | Personal data under the DPDP Act 2023 / GDPR Art. 4. |
| The consortium key | Derives every `risk_id`. Compromise is retroactive. |
| Risk signals | Determine whether a customer's money moves. |
| The audit trail | Evidence. Must be reconcilable and tamper-evident. |

Adversaries we design against, in rough order of likelihood:

1. **A fraudster** who controls a receiver (mule) account and wants to learn whether it has been flagged, or to get a flag removed.
2. **The bus operator** — honest-but-curious infrastructure that sees every message and every query.
3. **A malicious or compromised member bank** — holds a valid signing key and the consortium key. This is the hardest case and the one the design handles least well.
4. **An insider** who exfiltrates the consortium key once and never touches the system again.

## 2. What M0 does enforce

- **Raw account numbers never reach the bus.** Only `HMAC-SHA256(key, account)` is published.
- **Publisher authenticity.** Every `RiskSignal` is Ed25519-signed and verified against a registered key before storage. A member cannot publish under a peer's name, so it cannot retract a peer's flag. Signing end-to-end also means the bus operator cannot alter a signal undetected.
- **No cross-bank overwrite.** Signals are stored per `(risk_id, bank)`; the effective risk is the maximum live score. A member can only revise its own assessment.
- **Replay resistance.** A signal older than the one already held from that bank is rejected.
- **Bounded resources.** TTL is capped at 7 days, signals expire, the store is capped, and no member may occupy more than a configured share of it. Eviction drops the *least risky* entries, so a flood cannot displace genuine flags.
- **Fail-closed control paths.** A missing consortium key raises rather than defaulting. A bus outage is distinguished from a clean account and resolves to step-up, never to allow. The policy engine coerces its status input and *raises* on an unrecognised one: `allow` is only ever returned from a branch that decided on it, never as a fall-through.
- **Blocking requires justification.** A block freezes an innocent customer's payments for up to the full TTL with no retraction path, so a signal whose own publisher reports low confidence is capped at step-up. Corroboration across banks is configurable (`min_banks_to_block`), defaulting to 1 because acting on fraud *first seen elsewhere* is the point of the network.
- **Durable replay guard.** Per-`(account, bank)` high-water marks outlive the entries they protect, so flushing an entry out of the store cannot make a captured message replayable again.
- **No clock-skew slot reservation.** A publisher's claimed timestamp is clamped to arrival time for ordering, so a node stamping slightly ahead cannot suppress its own fraud engine's later, higher score.
- **Quotas reject rather than discard.** A publisher at its quota is told its signal did not land, instead of having one of its own live flags silently thrown away — which previously let a peer steer *which* flag was dropped.
- **No score oracle.** Customer-facing copy never carries the score, the flagging bank, or the `risk_id` — and neither does the object BankBot returns, which carries only the decision, the copy, and an opaque audit reference. Evidence is reachable only through the audit sink.
- **Idempotent decisions.** A retried `transfer_id` returns the decision already taken, so volatile bus state cannot produce two contradictory audit records under one key.
- **Audit drops are never silent.** The in-memory sink counts and logs every discarded record, so evidence of a blocked attempt cannot be flushed away by a flood of cheap allowed probes without leaving a trace.
- **Log-injection resistance.** Identifiers written to the audit trail are restricted to printable ASCII by allowlist, which covers the Unicode line separators (U+0085, U+2028, U+2029) that an ASCII control-character denylist misses.

## 3. Known limitations — accepted for M0

### 3.1 Pseudonymity does not hold against a member bank — *critical, by construction*

`risk_id` is a keyed hash of a low-entropy input, and every member holds the key. On
the order of 10⁹ live accounts can be enumerated offline on one GPU in well under a
minute, producing a permanent `risk_id → account` table.

So the identifier protects account numbers from **the bus operator**, and from anyone
who compromises the topic. It does **not** protect them from **a participating bank**.
A member can deanonymise the entire bus, and can monitor it to learn which of *its own*
customers a peer has flagged.

No hash function fixes this. A slow KDF (Argon2id) raises the one-time cost to roughly
the price of a coffee while making derivation too slow for the payment path — it is not
a fix. The real fix is an **OPRF (RFC 9497)**: an authority holds the key, banks send
blinded inputs, and no party can map the space alone. Each guess then costs one online,
rate-limited, attributable round trip instead of a free offline hash — turning an
unbounded, silent, retroactive compromise into a bounded, logged, prospective one.

**This is the honest version of the project's novelty claim, and it is a stronger one:**
the contribution is not "we hash the account number" — that is neither novel nor sound —
but a bus design whose privacy failure mode is explicit and bounded.

### 3.2 No key rotation support — *high*

There is no `key_epoch` field. Rotating the consortium key silently orphans every
existing signal: lookups miss, and a miss resolves to allow. Rotation, a partial
rollout, or one misconfigured node converts outstanding fraud blocks into allows with
no error and no alarm.

Because TTL is capped at 7 days and defaults to 24 hours, a signal is useless after a
day — so daily epoch keys would cost nothing functionally while capping how far back a
leaked key reaches. Needs: a `key_epoch` field, dual-epoch lookup during overlap, and a
canary signal per epoch so a node with the wrong key detects it.

### 3.3 The account number is not a globally unique key — *high*

`risk_id` is derived from the account number alone, which is unique only within a bank.
Two customers at different banks can share an account number and therefore a `risk_id`,
so a flag against one blocks the other. Keying on `(IFSC, account)` fixes it — with
**length-prefixed** encoding, since naive concatenation collides
(`"HDFC0001234"+"5678"` equals `"HDFC000123"+"45678"`).

### 3.4 Lookups leak the beneficiary graph — *high*

Every lookup tells the bus operator that a specific bank is about to pay a specific
(pseudonymous) account, right now. Over a day this reconstructs much of a bank's
beneficiary graph with timing and volume. Mitigation is cheap: send a short prefix of
the `risk_id` and filter locally, so the operator sees a bucket of several hundred
candidates rather than the one you wanted.

### 3.5 A flagged account can self-identify — *medium*

A fraudster who controls a mule account can trigger a flag deliberately, watch the bus,
and learn exactly which `risk_id` is theirs — no key and no compute required. Full-
precision `timestamp` and unrounded `confidence` also act as linkage tags that survive
key rotation. Mitigation: quantise timestamps, jitter publication, round `confidence`,
and bucket scores.

### 3.6 The key lives in an environment variable — *medium*

`ARIS_SALT` is readable from `/proc/<pid>/environ`, `ps eww`, container inspection, and
crash dumps. The project write-up describes HSM/KMS storage; **this code does not do
that**, and loading a key into process memory defeats the point of an HSM. Doing it
properly (PKCS#11 `C_Sign`) would mean the key cannot be exfiltrated at all — an
insider could only *use* it, at HSM throughput, leaving enumeration as days of
saturated, logged traffic.

The key is at least required to be encoded random bytes rather than a passphrase: given
one known `(account, risk_id)` pair, which every member has, a human-chosen passphrase
falls to off-the-shelf tooling.

### 3.7 The audit log is a plaintext reverse table — *medium*

`AuditRecord` stores `receiver_account` next to `risk_id`. Correct for investigations,
but it accumulates a ready-made mapping for every account a bank's customers pay. Needs
encryption at rest, a named investigations role, and a retention limit.

## 4. Priority order

| # | Change | Addresses | Effort |
| --- | --- | --- | --- |
| 1 | `key_epoch` + per-epoch canary | 3.2 | 0.5 d |
| 2 | Key on `(IFSC, account)`, length-prefixed | 3.3 | 0.5 d |
| 3 | Prefix-bucket lookup | 3.4 | 0.5 d |
| 4 | Quantise timestamp, round confidence | 3.5 | 1 h |
| 5 | OPRF-derived `risk_id` | 3.1 | 3–5 d |
| 6 | HSM-resident key | 3.6 | 2–3 d |

Items 1–4 are independent of the OPRF and worth doing regardless. If item 5 is out of
reach, HSM-resident HMAC plus daily epochs gets most of the benefit with no new
cryptography — the remaining gap is that each bank then rate-limits *itself*, whereas
an OPRF puts that limit in a counterparty's hands. That difference is the difference
between a control and a promise.

## 5. Reporting

This is student research, not a deployed system. Security issues: open an issue, or
contact the maintainers listed in [`TEAM.md`](TEAM.md).
