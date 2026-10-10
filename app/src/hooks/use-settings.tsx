/** Settings persistence + a shared API client (src/api/client.ts). */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import { View } from "react-native";

import { clearAuthBlock } from "@/sync/engine";
import { colors } from "@/theme";

import {
  loadSettings,
  persistSettings,
  TouchGrassClient,
  type AppSettings,
} from "@/api/client";

const DEFAULT_SETTINGS: AppSettings = { server1: "", server2: "", token: "" };

// Module-scoped settings view the API client reads. Single-user app: one
// client lives for the app's lifetime, and the provider keeps this view in
// sync so the client always sees the latest saved values.
const settingsView: { value: AppSettings } = { value: DEFAULT_SETTINGS };

type SettingsContextValue = {
  settings: AppSettings;
  ready: boolean;
  configured: boolean;
  save: (next: AppSettings) => Promise<void>;
  api: TouchGrassClient;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [ready, setReady] = useState(false);

  // Created once, lazily. The getter reads the module-scoped view only when
  // actually called, so the effect below is free to update it.
  const [api] = useState(() => new TouchGrassClient(() => settingsView.value));

  // The client reads settingsView, so update it synchronously whenever
  // settings change (not in an effect, which runs after children's effects).
  useEffect(() => {
    loadSettings()
      .then((loaded) => {
        settingsView.value = loaded;
        setSettings(loaded);
      })
      .catch(() => undefined)
      .finally(() => setReady(true));
  }, []);

  const save = useCallback(async (next: AppSettings) => {
    settingsView.value = next;
    setSettings(next);
    clearAuthBlock(); // a new token deserves a fresh sync attempt
    await persistSettings(next);
  }, []);

  const value = useMemo<SettingsContextValue>(
    () => ({
      settings,
      ready,
      configured: settings.server1.trim().length > 0,
      save,
      api,
    }),
    [settings, ready, save, api],
  );

  // Render nothing until the token/URLs are loaded from AsyncStorage, so no
  // screen can fire a request or build an <Image> with an empty token.
  if (!ready) return <View style={{ flex: 1, backgroundColor: colors.background }} />;

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const context = useContext(SettingsContext);
  if (!context) throw new Error("useSettings must be used inside <SettingsProvider>");
  return context;
}