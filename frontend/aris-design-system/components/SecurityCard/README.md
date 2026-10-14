# SecurityCard

A security card names one hardened area; it flips on hover, focus or click to show what was done and its stated limitation.

**Consumer provides:** `title`, `summary` (front), `done` (back), optional `limitation` (back, and an amber "Note" chip on the front), optional `noteLabel`.

- Front: `allow-100` check badge, `h3` title, `body-s` summary. Back: `surface-raised` with "What was done" and "Stated limitation" labels.
- Lay out the eight areas in a 4×2 grid on desktop, one column at 375px: key rotation, IFSC + account keying, prefix-bucketed lookups, timestamp quantisation, OPRF blind-signature IDs, HSM-resident key (PKCS#11), mTLS + per-bank Kafka ACLs, encrypted access-controlled audit log.
- State limitations plainly ("SoftHSM2 — real HSM hardware not available"). Describe components as implemented, never as production-deployed.
- The flip is 700ms ease-out; reduced motion swaps faces without rotation. Keyboard: Enter/Space toggles.
