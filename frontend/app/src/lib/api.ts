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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "The live demo server is not reachable.");
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<{ status: string; investigator_id: string }>("/demo/health"),
  reset: () => post<{ status: string }>("/demo/reset"),
  riskId: (receiver_ifsc: string, receiver_account: string) =>
    post<{ risk_id: string; network: NetworkView }>("/demo/risk-id", {
      receiver_ifsc,
      receiver_account,
    }),
  flag: (receiver_ifsc: string, receiver_account: string) =>
    post<FlagResult>("/demo/flag", { receiver_ifsc, receiver_account }),
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
