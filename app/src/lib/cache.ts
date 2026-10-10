/** Last-known server responses (stats, history), kept in AsyncStorage for offline display. */

import AsyncStorage from "@react-native-async-storage/async-storage";

export const STATS_CACHE_KEY = "tg:cache:stats";
export const HISTORY_CACHE_KEY = "tg:cache:history";
export const PREFERENCES_CACHE_KEY = "tg:cache:preferences";

export type CacheEnvelope<T> = { data: T; savedAt: string };

export async function readCache<T>(key: string): Promise<CacheEnvelope<T> | null> {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? (JSON.parse(raw) as CacheEnvelope<T>) : null;
  } catch {
    return null;
  }
}

export async function writeCache<T>(key: string, data: T): Promise<void> {
  try {
    const envelope: CacheEnvelope<T> = { data, savedAt: new Date().toISOString() };
    await AsyncStorage.setItem(key, JSON.stringify(envelope));
  } catch {
    // A failed cache write must never break the screen.
  }
}

/** "9:14 PM" for today, otherwise "Oct 9, 9:14 PM". */
export function formatUpdatedAt(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const day = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${day}, ${time}`;
}

export function staleText(iso: string): string {
  return `Last updated ${formatUpdatedAt(iso)}, offline`;
}
