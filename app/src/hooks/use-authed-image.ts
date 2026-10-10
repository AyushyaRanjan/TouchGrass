/**
 * Download a server image with the bearer token and return a local file:// URI.
 *
 * <Image source={{ headers }}> does not reliably send headers, so every
 * server-hosted image goes through this hook instead. Files are cached under
 * cacheDirectory/thumbs and reused when they already exist.
 *
 * If `localUri` is given (a photo that is still on the phone: a queued item
 * or a fresh preview) it is returned directly and nothing is downloaded.
 */

import * as FileSystem from "expo-file-system/legacy";
import { useEffect, useState } from "react";

import { useSettings } from "@/hooks/use-settings";

export function useAuthedImage(
  submissionId: number | null,
  thumb: boolean,
  localUri?: string | null,
): string | null {
  const { api, settings, ready } = useSettings();
  const token = settings.token.trim();

  // The result remembers which inputs it belongs to, so stale results are ignored.
  const key = `${submissionId}_${thumb ? 1 : 0}_${token}`;
  const [result, setResult] = useState<{ key: string; uri: string } | null>(null);

  useEffect(() => {
    if (localUri || submissionId === null) return;
    const setUri = (uri: string) => setResult({ key, uri });
    // Wait for the token to be loaded from AsyncStorage before downloading.
    if (!ready || !token || !FileSystem.cacheDirectory) return;

    let cancelled = false;
    const dir = `${FileSystem.cacheDirectory}thumbs/`;
    const path = `${dir}${submissionId}_${thumb ? 1 : 0}.jpg`;

    (async () => {
      try {
        await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => undefined);
        const info = await FileSystem.getInfoAsync(path);
        if (info.exists) {
          if (!cancelled) setUri(path);
          return;
        }
        const download = await FileSystem.downloadAsync(api.imageUrl(submissionId, thumb), path, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (download.status === 200) {
          if (!cancelled) setUri(download.uri);
          return;
        }
        // downloadAsync writes error bodies to disk too; never cache those.
        await FileSystem.deleteAsync(path, { idempotent: true });
        console.warn("image download failed", submissionId, `HTTP ${download.status}`);
      } catch (err) {
        await FileSystem.deleteAsync(path, { idempotent: true }).catch(() => undefined);
        console.warn(
          "image download failed",
          submissionId,
          err instanceof Error ? `${err.name}: ${err.message}` : String(err),
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [api, ready, token, submissionId, thumb, key, localUri]);

  if (localUri) return localUri;
  return result && result.key === key ? result.uri : null;
}
