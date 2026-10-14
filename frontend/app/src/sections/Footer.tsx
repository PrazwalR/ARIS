const REPO = "https://github.com/PrazwalR/ARIS/blob/main";

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-row">
        <div>
          <p className="wordmark">ARIS</p>
          <p className="body-s muted">
            Student research. Implemented components, not a production deployment.
          </p>
        </div>
        <nav aria-label="Documents" className="footer-links">
          <a className="ar-btn ar-btn-ghost ar-btn-sm" href={`${REPO}/docs/SECURITY.md`}>
            Security model
          </a>
          <a className="ar-btn ar-btn-ghost ar-btn-sm" href={`${REPO}/docs/METRICS.md`}>
            Metrics
          </a>
          <a className="ar-btn ar-btn-ghost ar-btn-sm" href={`${REPO}/docs/REVIEW_RUNBOOK.md`}>
            Review runbook
          </a>
        </nav>
      </div>
    </footer>
  );
}
