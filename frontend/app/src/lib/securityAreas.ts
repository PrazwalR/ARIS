/** The eight hardened areas of docs/SECURITY.md, stated as implemented, with
 * every limitation that document records. */
export interface SecurityArea {
  title: string;
  ref: string;
  done: string;
  limitation?: string;
}

export const SECURITY_AREAS: SecurityArea[] = [
  {
    title: "Key rotation",
    ref: "§3.2",
    done: "Daily HMAC subkeys from one root key; lookups check today's and yesterday's epoch; a per-epoch canary detects key drift.",
  },
  {
    title: "IFSC + account keying",
    ref: "§3.3",
    done: "IDs derive from the receiver's IFSC and account together, length-prefixed, so no two accounts collide.",
  },
  {
    title: "Prefix-bucketed lookups",
    ref: "§3.4",
    done: "256 Kafka partitions by ID prefix; a bank consumes only the buckets it uses.",
    limitation: "A cold lookup waits on one bucket's catch-up; p99 rises to hundreds of ms.",
  },
  {
    title: "Timestamp quantisation",
    ref: "§3.5",
    done: "Timestamps floored to the minute and confidence bucketed before signing.",
    limitation: "Publication jitter and score bucketing are not implemented.",
  },
  {
    title: "Blind-signature IDs (OPRF)",
    ref: "§3.1",
    done: "A consortium authority evaluates blinded queries it cannot read, rate-limits each bank, and is key-pinned by clients.",
    limitation: "RSA-FDH, not RFC 9497. Bank authentication is a shared secret, not mTLS.",
  },
  {
    title: "HSM-resident key",
    ref: "§3.6",
    done: "The root key is non-extractable inside a PKCS#11 token; a ceremony import gives every bank the same key.",
    limitation: "Tested on SoftHSM2. Real HSM hardware not available.",
  },
  {
    title: "mTLS + per-bank ACLs",
    ref: "§3.8",
    done: "Client certificates required; each bank may write only under its own identity; retention-based topic.",
    limitation: "Schema Registry is plain HTTP; a plaintext listener remains for development.",
  },
  {
    title: "Encrypted audit log",
    ref: "§3.7",
    done: "AES-256-GCM records; reads need a named investigator and reason, and are logged; retention enforced.",
    limitation: "The demo sink generates its key per process; production loads it from a KMS.",
  },
];
