/** Client for the showcase server (python -m aris.demo.server): the real
 * BankBot API under /api and the story's other actors under /demo. */

export type Decision = "allow" | "step_up" | "block";

export interface TransferInput {
  receiver_ifsc: string;
  receiver_account: string;
  amount_minor: number;
  user_ref?: string;
}

export interface TransferResult {
  decision: Decision;
  user_message: string;
  audit_ref: string;
  step_up_required: boolean;
}

export interface NetworkView {
  status: "found" | "not_found" | "unavailable";
  score: number | null;
  contributing_banks: string[];
}

export interface FlagResult {
  publisher: string;
  outcome: string;
  risk_id: string;
  key_epoch: number;
  signature: string;
  network: NetworkView;
}

/** What Bank B asserts when it flags; the server default is 92 at 0.94. */
export interface FlagScore {
  riskScore: number;
  confidence: number;
}

export interface ForgeResult {
  signer: string;
  claimed_publisher: string;
  result: string;
  reason: string | null;
  network: NetworkView;
}

export interface FeedRow {
  audit_ref: string;
  risk_id: string;
  decision: Decision;
  lookup_status: string;
  amount_minor: number;
  currency: string;
  decided_at: string;
}

export interface AuditRecord {
  transfer_id: string;
  risk_id: string;
  decision: Decision;
  lookup_status: string;
  risk_score: number | null;
  reason_codes: string[];
  contributing_banks: string[];
  model_version: string | null;
  amount_minor: number;
  decided_at: string;
  policy: { step_up_at: number; block_at: number };
}

export interface InvestigatorCredentials {
  id: string;
  key: string;
  reason: string;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** One console line per real request, so anyone can watch the page talk to
 * the server: method, path, status, time, and the outcome -- never request
 * bodies or credentials. The decision log's background polling is left out. */
function logRequest(method: string, path: string, status: number | "failed", ms: number, body?: unknown) {
  if (path === "/demo/feed") return;
  const b = (body ?? {}) as Record<string, unknown>;
  const outcome = b.decision ?? b.result ?? b.outcome ?? b.status ?? (typeof b.detail === "string" ? b.detail : "");
  console.info(
    `%cARIS%c ${method} ${path} → ${status}${outcome ? ` · ${String(outcome)}` : ""} (${Math.round(ms)} ms)`,
    "background:#16324f;color:#fff;padding:1px 5px;border-radius:2px",
    "",
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const started = performance.now();
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    logRequest(method, path, "failed", performance.now() - started);
    throw new ApiError(0, "The live demo server is not reachable.");
  }
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    body = undefined;
  }
  logRequest(method, path, resp.status, performance.now() - started, body);
  if (!resp.ok) {
    const detail = (body as { detail?: unknown } | undefined)?.detail;
    throw new ApiError(
      resp.status,
      typeof detail === "string" ? detail : `${resp.status} ${resp.statusText}`,
    );
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<{ status: string; investigator_id: string; bus_down: boolean }>("/demo/health"),
  reset: () => post<{ status: string }>("/demo/reset"),
  outage: (down: boolean) => post<{ bus_down: boolean }>("/demo/outage", { down }),
  riskId: (receiver_ifsc: string, receiver_account: string) =>
    post<{ risk_id: string; network: NetworkView }>("/demo/risk-id", {
      receiver_ifsc,
      receiver_account,
    }),
  flag: (receiver_ifsc: string, receiver_account: string, score?: FlagScore) =>
    post<FlagResult>("/demo/flag", {
      receiver_ifsc,
      receiver_account,
      ...(score ? { risk_score: score.riskScore, confidence: score.confidence } : {}),
    }),
  forge: (receiver_ifsc: string, receiver_account: string) =>
    post<ForgeResult>("/demo/forge", { receiver_ifsc, receiver_account }),
  feed: () => request<{ decisions: FeedRow[] }>("/demo/feed"),
  transfer: (input: TransferInput) =>
    post<TransferResult>("/api/transfers", {
      user_ref: input.user_ref ?? "anu",
      bank_id: "BANK-A",
      receiver_ifsc: input.receiver_ifsc,
      receiver_account: input.receiver_account,
      amount_minor: input.amount_minor,
    }),
  audit: (ref: string, who: InvestigatorCredentials) =>
    request<AuditRecord>(`/api/audit/${encodeURIComponent(ref)}`, {
      headers: {
        "X-Investigator-Id": who.id,
        "X-Investigator-Key": who.key,
        "X-Investigator-Reason": who.reason,
      },
    }),
};

/** The design system's StatusPill speaks "stepup"; the API says "step_up". */
export function pillDecision(d: Decision): "allow" | "stepup" | "block" {
  return d === "step_up" ? "stepup" : d;
}
