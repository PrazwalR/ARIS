import { TableWrap } from "../components/common";
import { PageHead, Panel } from "../components/Layout";
import type { MetricsData } from "../lib/data";
import { grouped } from "../lib/format";

const COMMANDS = `pytest                                    # full suite, live Kafka + SoftHSM2
python scripts/e2e_live_oprf.py           # 3-process OPRF end-to-end
python scripts/export_frontend_data.py    # re-measure everything on this site
cd frontend/app && npm test && npm run test:e2e`;

export function Evidence({ data }: { data: MetricsData }) {
  const t = data.tests;
  const generated = new Date(data.generated_at);
  const checks: [string, boolean | undefined, string][] = [
    ["Test suite", t ? t.failed === 0 && t.exit_code === 0 : undefined, t ? `${grouped(t.passed)} passed, ${t.failed} failed, ${t.skipped} skipped` : "not run"],
    ["Type check", data.checks?.mypy_strict, "mypy --strict"],
    ["Lint", data.checks?.ruff, "ruff"],
  ];
  return (
    <div className="container">
      <PageHead
        title="Evidence"
        lede="Every figure on this site was measured by one script, on one commit."
      />

      <div className="grid-2">
        <Panel title="Checks">
          <TableWrap label="Checks">
          <table className="data compact" data-testid="checks">
            <tbody>
              {checks.map(([name, ok, detail]) => (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td>
                    <span className={`sev ${ok === undefined ? "" : ok ? "sev-pass" : "sev-high"}`}>
                      {ok === undefined ? "Not measured" : ok ? "Pass" : "Fail"}
                    </span>
                  </td>
                  <td className="mono small" data-testid={name === "Test suite" ? "badge-tests" : undefined}>
                    {detail}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableWrap>
          <p className="small muted" data-testid="provenance">
            Measured {generated.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}
            {data.commit ? (
              <>
                {" "}
                on commit <span className="mono">{data.commit}</span>
              </>
            ) : null}
            .
          </p>
        </Panel>

        <Panel title="Bus load test">
          <TableWrap label="Bus load test">
          <table className="data compact" data-testid="load">
            <thead>
              <tr>
                <th scope="col">Backend</th>
                <th scope="col" className="num">Signals/s</th>
                <th scope="col" className="num">Lost updates</th>
                <th scope="col" className="num">Lookup p99</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["In-memory", data.load.memory],
                  ["Kafka (acks=all)", data.load.kafka],
                ] as const
              ).map(([name, r]) =>
                r ? (
                  <tr key={name}>
                    <th scope="row">{name}</th>
                    <td className="mono num">{grouped(Math.round(r.throughput_per_s))}</td>
                    <td className="mono num">{r.lost_updates}</td>
                    <td className="mono num">{r.lookup_p99_ms} ms</td>
                  </tr>
                ) : (
                  <tr key={name}>
                    <th scope="row">{name}</th>
                    <td colSpan={3} className="muted">
                      Not running during this export
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
          </TableWrap>
        </Panel>
      </div>

      <Panel title="Reproduce">
        <pre className="code">{COMMANDS}</pre>
      </Panel>
    </div>
  );
}
