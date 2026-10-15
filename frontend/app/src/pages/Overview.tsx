import { Link } from "../components/Link";
import type { MetricsData } from "../lib/data";
import { fixed, grouped } from "../lib/format";

const STEPS = [
  ["Publish", "A bank that confirms fraud publishes a signed score against a pseudonymous ID."],
  ["Verify", "The shared bus accepts a signal only if it verifies against the publisher's key."],
  ["Look up", "Before a transfer, the paying bank derives the same ID and queries the bus."],
  ["Decide", "Policy allows, asks for verification, or blocks. The customer never sees the score."],
] as const;

export function Overview({ data }: { data: MetricsData | null }) {
  const ulb = data?.datasets.ulb;
  return (
    <div className="container">
      <section className="intro">
        <h1 tabIndex={-1}>
          Shared fraud intelligence between banks, without sharing customer data.
        </h1>
        <p className="lede">
          ARIS lets banks exchange signed, pseudonymous risk signals and train one fraud model
          together. Account numbers, transactions and labels stay inside each bank.
        </p>
        <div className="actions">
          <Link to="/live" className="btn btn-primary">
            Run the live demo
          </Link>
          <Link to="/model" className="btn">
            View model results
          </Link>
        </div>
      </section>

      {data && ulb ? (
        <dl className="figures" data-testid="headline-figures">
          <div>
            <dt>Global AUC</dt>
            <dd>{fixed(ulb.global.metrics.auc)}</dd>
            <dd className="fig-note">ULB, full dataset · {fixed(ulb.mean_local.auc)} single bank</dd>
          </div>
          <div>
            <dt>Global PR-AUC</dt>
            <dd>{fixed(ulb.global.metrics.pr_auc)}</dd>
            <dd className="fig-note">{fixed(ulb.mean_local.pr_auc)} single bank</dd>
          </div>
          {data.tests ? (
            <div>
              <dt>Tests passing</dt>
              <dd>{grouped(data.tests.passed)}</dd>
              <dd className="fig-note">{data.tests.skipped === 0 ? "none skipped" : `${data.tests.skipped} skipped`}</dd>
            </div>
          ) : null}
          {data.load.kafka ? (
            <div>
              <dt>Kafka throughput</dt>
              <dd>{grouped(Math.round(data.load.kafka.throughput_per_s))}/s</dd>
              <dd className="fig-note">{data.load.kafka.lost_updates} lost updates</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      <section className="section">
        <h2>How it works</h2>
        <ol className="steps">
          {STEPS.map(([title, body], i) => (
            <li key={title}>
              <span className="step-no">{String(i + 1).padStart(2, "0")}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="section">
        <h2>System</h2>
        <div className="system" aria-label="System components">
          <div className="node">
            <span className="node-k">Bank B</span>
            <span className="node-v">Publisher · Ed25519 key</span>
          </div>
          <span className="edge" aria-hidden="true" />
          <div className="node node-core">
            <span className="node-k">Risk-signal bus</span>
            <span className="node-v">Kafka · mTLS · per-bank ACLs</span>
          </div>
          <span className="edge" aria-hidden="true" />
          <div className="node">
            <span className="node-k">Bank A · BankBot</span>
            <span className="node-v">Pre-transaction check</span>
          </div>
          <span className="edge" aria-hidden="true" />
          <div className="node">
            <span className="node-k">Audit log</span>
            <span className="node-v">AES-256-GCM · named access</span>
          </div>
        </div>
        <p className="note">
          IDs are derived by HMAC with daily key rotation, by a key held in an HSM, or by a blind-signature
          authority so no bank can compute them offline.{" "}
          <Link to="/security">Security controls</Link>
        </p>
      </section>
    </div>
  );
}
