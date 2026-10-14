import { LiveProvider } from "./lib/live";
import { useMetrics } from "./lib/data";
import { useTheme } from "./lib/ui";
import { Console } from "./sections/Console";
import { Federated } from "./sections/Federated";
import { Footer } from "./sections/Footer";
import { Header } from "./sections/Header";
import { Hero } from "./sections/Hero";
import { HowItWorks } from "./sections/HowItWorks";
import { Metrics } from "./sections/Metrics";
import { Proof } from "./sections/Proof";
import { Security } from "./sections/Security";

export default function App() {
  const [theme, setTheme] = useTheme();
  const metrics = useMetrics();

  return (
    <LiveProvider>
      <div id="top" />
      <Header theme={theme} onTheme={setTheme} />
      <main id="main" tabIndex={-1}>
        <Hero />
        <HowItWorks />
        <Console />
        {metrics.state === "ready" ? (
          <>
            <Federated data={metrics.data} />
            <Metrics data={metrics.data} />
          </>
        ) : (
          <MetricsStatus state={metrics.state} message={metrics.state === "error" ? metrics.message : ""} />
        )}
        <Security />
        {metrics.state === "ready" ? <Proof data={metrics.data} /> : null}
      </main>
      <Footer />
    </LiveProvider>
  );
}

function MetricsStatus({ state, message }: { state: "loading" | "error"; message: string }) {
  return (
    <section className="band band-plain" aria-live="polite">
      <div className="container">
        {state === "loading" ? (
          <p className="body muted">Loading measured results…</p>
        ) : (
          <p className="body error-text" role="alert" data-testid="metrics-error">
            Measured results could not be loaded ({message}). Generate them with{" "}
            <code className="mono-s">python scripts/export_frontend_data.py</code>. Nothing is
            shown in their place.
          </p>
        )}
      </div>
    </section>
  );
}
