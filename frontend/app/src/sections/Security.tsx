import { SecurityCard } from "@aris/components/aris.esm.js";
import { Section } from "../components/common";
import { SECURITY_AREAS } from "../lib/securityAreas";

export function Security() {
  return (
    <Section
      id="security"
      eyebrow="Security"
      title="Eight hardened areas, each with its stated limits"
      intro="Hover, focus or select a card to see what was done and what remains. These are implemented components, not a production deployment."
      band="alt"
    >
      <div className="security-grid" data-testid="security-grid">
        {SECURITY_AREAS.map((a) => (
          <SecurityCard
            key={a.title}
            title={a.title}
            summary={a.summary}
            done={a.done}
            limitation={"limitation" in a ? a.limitation : undefined}
          />
        ))}
      </div>
    </Section>
  );
}
