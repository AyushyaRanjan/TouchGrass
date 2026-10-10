/**
 * Starts the sync engine and keeps it running: on app open, when returning to
 * the foreground, when the network state changes, and every 60 s while open.
 */

import * as Network from "expo-network";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AppState } from "react-native";

import { queuedCount, resetStuckUploading, subscribe } from "@/db/local";
import { useSettings } from "@/hooks/use-settings";
import { getSyncState, subscribeSyncState, sync, type SyncState } from "@/sync/engine";

const INTERVAL_MS = 60 * 1000;

type SyncContextValue = SyncState & {
  queued: number;
  /** Sync now; `fresh` forgets retry backoff so waiting items go immediately. */
  syncNow: (fresh?: boolean) => Promise<void>;
};

const SyncContext = createContext<SyncContextValue | null>(null);

export function SyncProvider({ children }: { children: ReactNode }) {
  const { api, settings, configured } = useSettings();
  const token = settings.token.trim();
  const [engine, setEngine] = useState<SyncState>(getSyncState());
  const [queued, setQueued] = useState(0);

  useEffect(() => subscribeSyncState(() => setEngine(getSyncState())), []);

  // Keep the "waiting" count fresh whenever the local store changes.
  useEffect(() => {
    const refresh = () => void queuedCount().then(setQueued).catch(() => undefined);
    refresh();
    return subscribe(refresh);
  }, []);

  const syncNow = useCallback(
    async (fresh = false) => {
      if (!configured) return;
      await sync(api, token, { resetBackoff: fresh });
    },
    [api, token, configured],
  );

  useEffect(() => {
    if (!configured) return;
    let active = true;
    // App open: stuck "uploading" items go back to the queue, then sync.
    void resetStuckUploading().then(async () => {
      if (active) await syncNow(true);
    });

    const timer = setInterval(() => void syncNow(), INTERVAL_MS);
    const appState = AppState.addEventListener("change", (s) => {
      if (s === "active") void syncNow(true);
    });
    const network = Network.addNetworkStateListener((event) => {
      if (event.isConnected) void syncNow(true);
    });
    return () => {
      active = false;
      clearInterval(timer);
      appState.remove();
      network.remove();
    };
  }, [configured, syncNow]);

  const value = useMemo(() => ({ ...engine, queued, syncNow }), [engine, queued, syncNow]);
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncContextValue {
  const context = useContext(SyncContext);
  if (!context) throw new Error("useSync must be used inside <SyncProvider>");
  return context;
}
