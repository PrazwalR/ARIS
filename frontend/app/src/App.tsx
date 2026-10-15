import { Footer, Header, PageHead } from "./components/Layout";
import { useMetrics } from "./lib/data";
import type { MetricsData } from "./lib/data";
import { LiveProvider } from "./lib/live";
import { Link } from "./components/Link";
import { usePageFocus, usePath } from "./lib/router";
import { useTheme } from "./lib/ui";
import { Evidence } from "./pages/Evidence";
import { Live } from "./pages/Live";
import { Model } from "./pages/Model";
import { Overview } from "./pages/Overview";
import { Security } from "./pages/Security";

export default function App() {
  const [theme, setTheme] = useTheme();
  const metrics = useMetrics();
  const path = usePath();
  usePageFocus(path);
  const data = metrics.state === "ready" ? metrics.data : null;

  return (
    <LiveProvider>
      <Header theme={theme} onTheme={setTheme} />
      <main id="main" className="main">
        <Page path={path} data={data} state={metrics.state} />
      </main>
      <Footer />
    </LiveProvider>
  );
}

function Page(props: { path: string; data: MetricsData | null; state: string }) {
  const { path, data } = props;
  if (path === "/") return <Overview data={data} />;
  if (path === "/live") return <Live />;
  if (path === "/security") return <Security />;
  if (path === "/model" || path === "/evidence") {
    if (!data) return <DataStatus failed={props.state === "error"} />;
    return path === "/model" ? <Model data={data} /> : <Evidence data={data} />;
  }
  return (
    <div className="container">
      <PageHead title="Page not found" lede="That address does not exist on this site." />
      <Link to="/" className="btn">
        Back to overview
      </Link>
    </div>
  );
}

function DataStatus({ failed }: { failed: boolean }) {
  return (
    <div className="container">
      {failed ? (
        <PageHead
          title="Results unavailable"
          lede="Measured results could not be loaded. Generate them with python scripts/export_frontend_data.py."
        />
      ) : (
        <p className="muted">Loading measured results…</p>
      )}
    </div>
  );
}
