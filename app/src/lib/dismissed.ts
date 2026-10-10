/**
 * Result cards the user closed. Kept in memory (updated synchronously, so Close
 * is instant) and mirrored to AsyncStorage so a restart or a sync never re-opens them.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

const KEY = "tg:dismissed";
const MAX_IDS = 100;

let ids: string[] = [];
let loaded: Promise<void> | null = null;
const listeners = new Set<() => void>();

function ensureLoaded(): Promise<void> {
  if (!loaded) {
    loaded = AsyncStorage.getItem(KEY)
      .then((raw) => {
        const stored: string[] = raw ? (JSON.parse(raw) as string[]) : [];
        // Anything dismissed while loading is already in `ids`.
        ids = [...stored.filter((id) => !ids.includes(id)), ...ids].slice(-MAX_IDS);
      })
      .catch(() => undefined);
  }
  return loaded;
}

/** Read the stored ids once, then return the current set. */
export async function loadDismissed(): Promise<Set<string>> {
  await ensureLoaded();
  return new Set(ids);
}

export function isDismissed(id: string): boolean {
  return ids.includes(id);
}

/** Mark a submission dismissed. Synchronous for the caller; persistence runs in the background. */
export function dismiss(id: string): Set<string> {
  if (!ids.includes(id)) ids = [...ids, id].slice(-MAX_IDS);
  void ensureLoaded().then(() => AsyncStorage.setItem(KEY, JSON.stringify(ids)).catch(() => undefined));
  listeners.forEach((l) => l());
  return new Set(ids);
}

export function subscribeDismissed(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
