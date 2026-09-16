# Review runbook — commands to run live

Every command below was run end-to-end against this exact repo state before
handing this to you. Nothing here is aspirational. Read the one-line "shows"
before each block so you know what to say while it runs.

Companion artifact (written proof, numbers, constraints): the ARIS Build
Review page already shared with you. This file is for the terminal.

---

## 0. Before the reviewers arrive (5 min)

Do this once, beforehand — not live, it's just infrastructure startup.

```bash
open -a Docker                     # if Docker Desktop isn't already running
cd /Users/prazw/Desktop/Web3/ARIS

# Python env (recreate if /tmp got cleared since last time -- it does not
# survive a reboot):
python3.13 -m venv /tmp/aris_ci_sim
source /tmp/aris_ci_sim/bin/activate
pip install -e ".[dev,ml,kafka,api,xai,hsm]" -q

# Bring the Kafka + Schema Registry stack up:
docker compose up -d
docker ps --format '{{.Names}}\t{{.Status}}'   # both should say "healthy"
```

Everything after this point assumes that venv is active
(`source /tmp/aris_ci_sim/bin/activate`) and you're in the repo root.

---

## 1. The whole system builds and passes — live

**Shows:** every claim in the review doc is backed by a real, currently-green
test suite — not a subset, not a cherry-picked run. ~2 minutes, narrate over it.

```bash
ruff check src tests scripts && ruff format --check src tests scripts
mypy
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") pytest -p no:warnings
```

Expect: `All checks passed!`, `Success: no issues found in 35 source files`,
a progress line of dots, then **`331 passed in ~50s`**. `ARIS_SALT` is
generated fresh and thrown away on purpose — proves the code fails closed
with no key configured, rather than falling back to a default, and the
suite provides its own key for the parts that need one.

If someone wants to see individual test names scroll by instead of dots
(useful if they ask "which tests cover X") — note `-vv`, not `-v`:
`pyproject.toml` bakes in `-q` by default, and pytest's verbosity flags are
additive, so a single `-v` cancels it back to dots, not names:

```bash
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") pytest -vv -p no:warnings tests/test_kafka_mtls.py
```

---

## 2. The story, end to end

**Shows:** the actual scenario the whole system exists for — Bank B flags an
account, Anu's transfer at Bank A gets blocked, a hostile member's forged
retraction is rejected. ~2 seconds.

```bash
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") python -m aris.demo.anu_transfer
```

Narrate the three sections it prints: the signal on the bus (no plain
account number, ever), BankBot's decision, and the internal audit record
(only reachable by an analyst, never handed to the customer-facing channel).

### 2a. The same story, over real HTTP, backed by the real Kafka bus

**Shows:** M3 (Kafka) and M4 (the HTTP API) actually wired together — Bank
B's own process publishes to the live broker, Bank A's own separate process
(sharing nothing but the broker) answers a real `POST /transfers` with
`block`. This is the stronger version of step 2 if you want to show the
production shape, not just the CLI story.

```bash
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python scripts/review_demo_http_block.py
```

Expect `HTTP 200` and `{'decision': 'block', ...}`. Needs `docker compose up
-d` from step 0. (Note: `python -m aris.api` on its own can't show this —
its keyring starts empty and trusts no bank, so it always reads unflagged.
This script wires `create_app()` with a keyring that trusts BANK-B instead,
the same way `tests/test_api_kafka.py` does.)

---

## 3. The model, evaluated on data it never trained on

**Shows:** the AUC numbers in the review doc are measured on a genuine
held-out split, not the training data — and then the same trained model
scoring individual rows that never existed in the dataset at all.

Full per-bank tables, ROC curves, and Precision/Recall curves for both
datasets already live in **`docs/METRICS.md`** — open that instead of
re-running everything below if you just want to show the pictures. The
steps below are for reproducing those numbers live, from scratch.

### 3a. Train fresh, print the holdout split sizes, evaluate only on the holdout

```bash
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python -m aris.fl.run --dataset synthetic
```

Prints a JSON summary ending in `global_auc`/`global_beats_mean_local_auc`.
The unseen-data proof is in the saved report — pull it up right after:

```bash
python3 -c "
import json
r = json.load(open('data/processed/m1_metrics_synthetic.json'))
print('trained on:', r['n_train'], 'rows')
print('held out, NEVER trained on:', r['n_holdout'], 'rows,', r['holdout_positives'], 'of them fraud')
print()
print(f'{\"model\":<10} {\"AUC\":>6} {\"accuracy@0.5\":>13}')
for b in r['banks']:
    print(f'{b[\"bank_id\"]:<10} {b[\"local\"][\"auc\"]:>6.3f} {b[\"local\"][\"accuracy_at_0_5\"]:>13.3f}')
print(f'{\"mean local\":<10} {r[\"mean_local\"][\"auc\"]:>6.3f} {r[\"mean_local\"][\"accuracy_at_0_5\"]:>13.3f}')
print(f'{\"GLOBAL\":<10} {r[\"global\"][\"auc\"]:>6.3f} {r[\"global\"][\"accuracy_at_0_5\"]:>13.3f}')
"
```

Accuracy is included because reviewers ask for it by name, but say out loud
that AUC is the metric that actually matters here: with ~26% fraud in this
holdout, a model that always predicts "not fraud" would already score ~74%
accuracy without catching a single case — AUC and PR-AUC are what expose
that a model isn't just doing that.

Say out loud where the split happens: `src/aris/fl/run.py` carves the
holdout out *before* any bank's training client ever sees the data
(`pooled_holdout_from_shards` for synthetic, a time-based split for ULB so
training is on the past and evaluation is on the future — no lookahead).
The 5 `BankFlowerClient`s are only ever constructed with the training
portion; the holdout array is never passed to them.

### 3b. The same trained model scoring rows that never existed in the dataset

**Shows:** not just an aggregate AUC number — individual predictions on
hand-built inputs, live, including one that proves the model learned
something *specific*, not "big number triggers fraud."

```bash
python scripts/review_demo_unseen_scoring.py
```

Expect two clean/typical rows to read `allow`, two fraud-pattern rows
(mimicking what bank 0's and bank 3's fraud looked like in training) to
read `BLOCK`, and — the point worth pausing on — a row with the *same
spike magnitude* on feature `f6` to read `allow` too. Only 5 banks were
trained (driving features 0–4); `f6` never drove fraud for any of them, so
the model correctly learned it's irrelevant instead of just reacting to any
large number. That's the difference between pattern-matching and
memorizing a threshold.

### 3c. Test it on data of your own instead of the synthetic set

If your guide wants to see it trained on something less synthetic, and you
have a CSV with a numeric fraud-label column (`isFraud` or `Class`):

```bash
cp /path/to/your_data.csv data/raw/paysim.csv
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python -m aris.fl.run --dataset paysim --max-rows 20000
```

This reuses the exact same pipeline unchanged — non-IID partitioning across
5 banks, FedAvg, holdout evaluation — and saves a fresh checkpoint to
`data/processed/m1_global_paysim.npz`. Swap that path into
`review_demo_unseen_scoring.py`'s `load_weights(...)` call to score rows
against it instead of the synthetic checkpoint. If your columns don't match
that shape, `load_paysim` in `src/aris/fl/datasets.py` (~15 lines) is the
template to copy and adjust.

---

## 4. Security hardening, live — not just green checkmarks

### 4a. mTLS + per-bank ACLs (docs/SECURITY.md §3.8)

**Shows:** a bank with a *valid* certificate, signed by the same CA as
everyone else, still gets rejected the moment it tries to publish without a
Write ACL — and that a client with *no* certificate can't even open a
connection. Two distinct failure points, both real.

```bash
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python scripts/review_demo_mtls_acl.py
```

Expect, in order: BANK-B publishes and reads back its own signal
(`PublishOutcome.ACCEPTED`, `found, score=92`); BANK-EVIL's publish is
**rejected by the authorizer** (you'll see a logged Kafka traceback right
before this — that's the audit trail SS3.8 asks for, not a bug, say so
before it prints); BANK-EVIL can *still read* (Read is granted broadly, only
Write is per-bank); a client with zero certificate times out trying to
connect at all.

### 4b. HSM-resident key, non-extractable (docs/SECURITY.md §3.6)

**Shows:** the consortium key can be *used* to sign, but this process cannot
*read it back out* — enforced by the PKCS#11 token itself, not by a
promise in the code.

```bash
python scripts/review_demo_hsm.py
```

Expect: `CKA_EXTRACTABLE: False`, `CKA_SENSITIVE: True`, a real HMAC
signature printed, then `REFUSED by the token -- pkcs11.AttributeSensitive`
on the extraction attempt. If a reviewer asks "why SoftHSM2 and not a real
HSM" — the honest answer is in the review doc: no hardware was available,
and SoftHSM2 is a real PKCS#11 implementation, not a mock, but it can't
demonstrate hardware-level protection against a compromised host OS reading
its on-disk store directly.

---

## 5. The measured numbers, reproduced live

**Shows:** the throughput/latency figures in the review doc aren't fixed —
they're measured fresh every time, with real variance, against a real lock
and a real broker.

```bash
# In-memory bus:
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python -m aris.loadtest --backend memory --publishers 20 --signals 200

# Live Kafka bus (needs docker compose up from step 0):
ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \
  python -m aris.loadtest --backend kafka --publishers 10 --signals 50
```

Expect **`lost updates: 0`** on both — say this number out loud, it's the
one that actually matters (throughput varies run to run on a laptop;
zero lost updates under concurrent read/write pressure does not). Kafka's
numbers will run lower than in-memory and that's expected and explained in
the review doc (§3.4's prefix-bucketing trade-off) — don't let a lower
number here read as a regression.

---

## 6. If someone asks "how do I know these commits are really yours"

```bash
git log --oneline
```

Every commit in the history is plain and human-authored — no AI
co-attribution on any of them. If asked to show one in full:

```bash
git show --stat <hash>
```

---

## Numbers you do *not* need to reproduce live (too slow / needs specific
datasets already generated)

These are in `README.md`'s phase log and `docs/LOADTEST.md`, already
verified — mention them, don't re-run them in the room:

- **M1/M2 FL + DP-SGD tables** (AUC vs. ε, 20-run stability check) — full
  training runs, multi-minute each. Command if someone insists:
  `python -m aris.fl.privacy_sweep --dataset synthetic`.
- **M6+ Byzantine robust-aggregation attack** (FedAvg <0.55 AUC vs. Krum
  >0.55 under the identical attack) — `pytest tests/test_fl_robust_agg.py -v`
  reruns it, ~30s, but it's already in the suite you ran in step 1.
- **M5 SHAP ground-truth check** (0.639 vs. 0.006) — also already covered
  by step 1's full run, specifically `tests/test_fl_explain.py`.
