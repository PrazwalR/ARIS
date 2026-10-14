import { GlassCard, Icon, MetricTile, TerminalCard } from "@aris/components/aris.esm.js";
import type { ReactNode } from "react";
import { Section } from "../components/common";
import type { MetricsData } from "../lib/data";
import { grouped } from "../lib/format";

export function Proof({ data }: { data: MetricsData }) {
  const t = data.tests;
  const mem = data.load.memory;
  const kafka = data.load.kafka;
  const generated = new Date(data.generated_at);
  return (
    <Section
      id="proof"
      eyebrow="Proof"
      title="Run it yourself"
      intro="The figures on this page were measured together, by one script, on one commit."
    >
      <div className="proof-grid">
        <div className="proof-terminal">
          {t ? (
            <TerminalCard command={t.command} result={t.summary} title="aris — test suite" />
          ) : (
            <p className="body-s muted">The test suite was not run for this export.</p>
          )}
          <p className="body-s muted provenance" data-testid="provenance">
            Measured {generated.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}
            {data.commit ? (
              <>
                {" "}
                on commit <span className="mono-s">{data.commit}</span>
              </>
            ) : null}{" "}
            by <span className="mono-s">scripts/export_frontend_data.py</span>.
          </p>
        </div>
        <div className="proof-badges">
          {t ? (
            <Badge testId="badge-tests" ok={t.failed === 0 && t.exit_code === 0}>
              {grouped(t.passed)} tests passed
              {t.failed ? `, ${t.failed} failed` : ""}
              {t.skipped ? `, ${t.skipped} skipped` : ""}
            </Badge>
          ) : null}
          {t && t.skipped === 0 && t.passed > 0 ? (
            <Badge>Kafka, mTLS and HSM suites ran live, none skipped</Badge>
          ) : null}
          {data.checks?.mypy_strict ? <Badge>mypy strict: clean</Badge> : null}
          {data.checks?.ruff ? <Badge>ruff: clean</Badge> : null}
        </div>
      </div>

      <div className="tile-grid load-tiles" data-testid="load-tiles">
        <GlassCard lift={false}>
          <MetricTile
            value={mem.throughput_per_s}
            decimals={0}
            unit="signals/s"
            label="In-memory bus"
            context={
              <>
                {mem.publishers} concurrent publishers, <b>{mem.lost_updates}</b> lost updates
              </>
            }
          />
        </GlassCard>
        <GlassCard lift={false}>
          {kafka ? (
            <MetricTile
              value={kafka.throughput_per_s}
              decimals={0}
              unit="signals/s"
              label="Live Kafka bus"
              context={
                <>
                  acks=all, <b>{kafka.lost_updates}</b> lost updates, lookup p99{" "}
                  <b>{kafka.lookup_p99_ms} ms</b>
                </>
              }
            />
          ) : (
            <p className="body-s muted">
              Kafka was not running for this export, so no Kafka figure is shown.
            </p>
          )}
        </GlassCard>
      </div>
    </Section>
  );
}

/** Allow-tinted proof badge; a failing result switches to the block tint
 * and icon rather than staying green. */
function Badge({ children, ok = true, testId }: { children: ReactNode; ok?: boolean; testId?: string }) {
  return (
    <span className={ok ? "badge" : "badge badge-fail"} data-testid={testId}>
      <Icon name={ok ? "check" : "alert"} />
      {children}
    </span>
  );
}
