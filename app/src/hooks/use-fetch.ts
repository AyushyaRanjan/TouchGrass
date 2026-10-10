/** A tiny data-fetching hook with loading / error state. Feed it your own error text. */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { readCache, writeCache } from "@/lib/cache";

export type FetchState<T> = {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  /** When the cached (or just-fetched) value was saved. */
  updatedAt: string | null;
  /** True when we are showing a cached value because the latest fetch failed. */
  stale: boolean;
  /** Re-run the fetcher. Stable identity, so effects may depend on it. */
  load: () => Promise<void>;
};

/**
 * `fetcher` is expected to be stable (e.g. closing over the shared API
 * client). Screens that render new data often re-run `load` on focus.
 * Pass `cacheKey` to seed from / write to AsyncStorage so the screen
 * still has data when the server is unreachable.
 */
export function useFetch<T>(
  fetcher: () => Promise<T>,
  options: { cacheKey?: string } = {},
): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const fetcherRef = useRef(fetcher);
  const cacheKeyRef = useRef(options.cacheKey);
  const dataRef = useRef<T | null>(null);
  const seeded = useRef(false);
  const requestId = useRef(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  useEffect(() => {
    cacheKeyRef.current = options.cacheKey;
  }, [options.cacheKey]);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    const key = cacheKeyRef.current;

    // Seed from cache before the network call so a known-good value
    // is on screen (no spinner) while we try to refresh.
    if (key && !seeded.current) {
      seeded.current = true;
      const cached = await readCache<T>(key);
      if (cached && id === requestId.current) {
        dataRef.current = cached.data;
        setData(cached.data);
        setUpdatedAt(cached.savedAt);
      }
    }

    if (!dataRef.current) setLoading(true);
    setError(null);
    try {
      const result = await fetcherRef.current();
      if (id !== requestId.current) return;
      dataRef.current = result;
      setData(result);
      setStale(false);
      setError(null);
      if (key) {
        const savedAt = new Date().toISOString();
        setUpdatedAt(savedAt);
        await writeCache(key, result);
      }
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof ApiError ? err : new ApiError("Something unexpected went wrong."));
      if (dataRef.current) setStale(true);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  return { data, loading, error, updatedAt, stale, load };
}
