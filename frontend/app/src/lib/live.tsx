import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import { LiveContext } from "./liveContext";
import type { BackendStatus } from "./liveContext";



export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [investigatorId, setInvestigatorId] = useState<string | null>(null);
  const [lens, setLens] = useState(false);
  const [ids, setIds] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const [feedVersion, setFeedVersion] = useState(0);
  const [lastAuditRef, setLastAuditRef] = useState<string | null>(null);

  const probe = useCallback(() => {
    api
      .health()
      .then((h) => {
        setInvestigatorId(h.investigator_id);
        setStatus("online");
      })
      .catch(() => setStatus("offline"));
  }, []);

  // Initial state is already "checking"; the effect only syncs with the server.
  useEffect(probe, [probe]);

  const recheck = useCallback(() => {
    setStatus("checking");
    probe();
  }, [probe]);

  const riskIdFor = useCallback(
    (ifsc: string, account: string) => {
      const key = `${ifsc}|${account}`;
      if (ids[key]) return ids[key];
      if (lens && status === "online" && !pending.current.has(key)) {
        pending.current.add(key);
        api
          .riskId(ifsc, account)
          .then((r) => setIds((prev) => ({ ...prev, [key]: r.risk_id })))
          .catch(() => undefined)
          .finally(() => pending.current.delete(key));
      }
      return undefined;
    },
    [ids, lens, status],
  );

  const bumpFeed = useCallback(() => setFeedVersion((v) => v + 1), []);

  const value = useMemo(
    () => ({
      status,
      investigatorId,
      recheck,
      lens,
      setLens,
      riskIdFor,
      feedVersion,
      bumpFeed,
      lastAuditRef,
      setLastAuditRef,
    }),
    [status, investigatorId, recheck, lens, riskIdFor, feedVersion, bumpFeed, lastAuditRef],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
