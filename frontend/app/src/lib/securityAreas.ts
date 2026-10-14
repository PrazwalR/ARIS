/** The eight hardened areas of docs/SECURITY.md, stated as implemented, with
 * every limitation that document records. */
export const SECURITY_AREAS = [
  {
    title: "Key rotation",
    summary: "risk_id keys rotate every UTC day; a leaked day's key exposes no other day.",
    done: "An HMAC subkey per UTC day is derived from one root key. Signals record their key_epoch, lookups try today's epoch then yesterday's, and a per-epoch canary catches a node whose key has drifted.",
  },
  {
    title: "IFSC + account keying",
    summary: "Account numbers are unique only within a bank, so the id covers both.",
    done: "risk_id is derived from the receiver's IFSC and account together, each length-prefixed, so no two accounts at different banks, and no crafted pair of strings, can collide.",
  },
  {
    title: "Prefix-bucketed lookups",
    summary: "A bank downloads only the slices of the bus it actually asks about.",
    done: "The Kafka topic is split into 256 buckets by risk_id prefix. Each instance consumes only buckets it has published to or looked up, instead of replicating every other bank's ids.",
    limitation: "A cold lookup waits on a bounded catch-up of one bucket; p99 lookup latency rises to hundreds of milliseconds.",
  },
  {
    title: "Timestamp quantisation",
    summary: "Signals carry coarse times so a flagged account can't spot itself by timing.",
    done: "Signal timestamps are floored to one-minute buckets and confidence is rounded before signing.",
    limitation: "Publication jitter and score bucketing are still open.",
  },
  {
    title: "OPRF blind-signature IDs",
    summary: "No bank can compute a risk_id offline; each query is blinded and rate-limited.",
    done: "A consortium authority evaluates RSA-FDH blind signatures over a blinded value it cannot read, rate-limits and logs each bank, and runs as its own service. Banks pin its key fingerprint.",
    limitation: "RSA-FDH, not RFC 9497. Banks authenticate to the authority with a shared secret over HTTP, not mTLS.",
  },
  {
    title: "HSM-resident key (PKCS#11)",
    summary: "The consortium key can be used to sign but never read back out.",
    done: "The root key is generated inside a PKCS#11 token as sensitive and non-extractable; ids are derived through C_Sign. BankBot and the API can run against it.",
    limitation: "SoftHSM2. Real HSM hardware not available.",
  },
  {
    title: "mTLS + per-bank Kafka ACLs",
    summary: "Only certificate-holding banks connect; only granted banks can publish.",
    done: "The broker requires client certificates and a standard authorizer maps each certificate to a bank. A bank with a valid certificate but no Write grant is refused by the authorizer.",
    limitation: "Schema Registry is still plain HTTP, and a plaintext listener remains for local development.",
  },
  {
    title: "Encrypted audit log",
    summary: "Evidence is encrypted at rest and readable only by named investigators.",
    done: "Records are AES-256-GCM encrypted with the audit reference bound in. Reading one needs a named investigator and a reason; every read is logged, and records expire after a retention window.",
    limitation: "The demo sink generates its key per process; a durable deployment loads it from a KMS.",
  },
] as const;
