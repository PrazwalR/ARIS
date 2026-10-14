import { createContext, useContext } from "react";

export type BackendStatus = "checking" | "online" | "offline";

export interface LiveState {
  status: BackendStatus;
  investigatorId: string | null;
  recheck: () => void;
  /** Privacy lens: every account number on the page is replaced by the
   * pseudonymous risk_id the network actually sees. */
  lens: boolean;
  setLens: (on: boolean) => void;
  riskIdFor: (ifsc: string, account: string) => string | undefined;
  /** Bumped after any action that writes a decision, so the feed refetches. */
  feedVersion: number;
  bumpFeed: () => void;
  lastAuditRef: string | null;
  setLastAuditRef: (ref: string) => void;
}

export const LiveContext = createContext<LiveState | null>(null);

export function useLive(): LiveState {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside <LiveProvider>");
  return ctx;
}
