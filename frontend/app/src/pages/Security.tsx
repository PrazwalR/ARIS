import { TableWrap } from "../components/common";
import { PageHead, Panel } from "../components/Layout";
import { SECURITY_AREAS } from "../lib/securityAreas";

const DOC = "https://github.com/PrazwalR/ARIS/blob/main/docs/SECURITY.md";

const REVIEW = [
  ["Truncated dataset accepted silently; published ULB figures inflated", "High"],
  ["Previous-epoch outage resolved to allow", "High"],
  ["Reused transfer ID carried a new payment under an old decision", "High"],
  ["Kafka compaction let a member erase a peer's flag", "High"],
  ["One member could mask or falsely corroborate a block", "Medium"],
  ["DP-SGD epsilon understated by a label-derived class weight", "Medium"],
  ["HSM mode could not share a key between banks", "Medium"],
] as const;

export function Security() {
  return (
    <div className="container">
      <PageHead
        title="Security"
        lede="Implemented controls and their stated limits. Components, not a production deployment."
      >
        <a className="btn" href={DOC}>
          Full security model
        </a>
      </PageHead>

      <Panel title="Controls">
        <TableWrap label="Security controls">
          <table className="data controls" data-testid="controls">
            <thead>
              <tr>
                <th scope="col">Control</th>
                <th scope="col">Implementation</th>
                <th scope="col">Stated limitation</th>
              </tr>
            </thead>
            <tbody>
              {SECURITY_AREAS.map((a) => (
                <tr key={a.title}>
                  <th scope="row">
                    {a.title}
                    <span className="ref">{a.ref}</span>
                  </th>
                  <td>{a.done}</td>
                  <td className={a.limitation ? "limit" : "muted"}>{a.limitation ?? "None recorded"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Panel>

      <Panel
        title="Adversarial review, October 2026"
        aside={<span className="small muted">all fixed, each with a regression test</span>}
      >
        <p className="small intro-line">
          Three independent bug-bounty passes and three test-writing passes. Selected findings:
        </p>
        <table className="data compact" data-testid="review">
          <thead>
            <tr>
              <th scope="col">Finding</th>
              <th scope="col">Severity</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {REVIEW.map(([finding, sev]) => (
              <tr key={finding}>
                <td>{finding}</td>
                <td>
                  <span className={`sev sev-${sev.toLowerCase()}`}>{sev}</span>
                </td>
                <td>Fixed</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
