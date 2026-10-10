/**
 * Typed fetch client for the Touch Grass server (see server/main.py).
 *
 * - Tries candidate base URLs in order: the last one that worked first,
 *   then Server URL 1, then Server URL 2 (deduped). The 5s timeout applies
 *   only to /health probes during selection; normal requests get 15s and
 *   uploads 90s.
 * - Sends `Authorization: Bearer <token>` when a token is configured.
 * - Remembers the base URL that last returned a success (AsyncStorage),
 *   so after a Tailscale -> LAN handoff the app starts on the right one.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

import type {
  Health,
  HistoryResponse,
  HonorResponse,
  Preferences,
  PreferencesPatch,
  RerollResponse,
  Stats,
  Submission,
  SubmissionCreate,
  SubmitInput,
  TasksResponse,
} from "./types";

export type AppSettings = {
  server1: string;
  server2: string;
  token: string;
};

export const STORAGE = {
  server1: "tg:server1",
  server2: "tg:server2",
  token: "tg:token",
  lastBaseUrl: "tg:lastBaseUrl",
} as const;

// The short timeout is ONLY for /health probing and server-URL selection.
const PROBE_TIMEOUT_MS = 5000;
const REQUEST_TIMEOUT_MS = 15000;
const UPLOAD_TIMEOUT_MS = 90000;

function trimBase(raw: string | null | undefined): string {
  if (!raw) return "";
  const trimmed = raw.trim().replace(/\/+$/, "");
  return trimmed;
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    const [[, server1], [, server2], [, token]] = await AsyncStorage.multiGet([
      STORAGE.server1,
      STORAGE.server2,
      STORAGE.token,
    ]);
    return { server1: server1 ?? "", server2: server2 ?? "", token: token ?? "" };
  } catch {
    return { server1: "", server2: "", token: "" };
  }
}

export async function persistSettings(settings: AppSettings): Promise<void> {
  await AsyncStorage.multiSet([
    [STORAGE.server1, settings.server1.trim()],
    [STORAGE.server2, settings.server2.trim()],
    [STORAGE.token, settings.token.trim()],
  ]);
}

/** Error that surfaces the server's `detail` message when one exists. */
export class ApiError extends Error {
  readonly status?: number;
  readonly base?: string;
  /** Name of the underlying error, e.g. AbortError or TypeError. */
  readonly causeName?: string;
  readonly timedOut: boolean;

  constructor(
    message: string,
    opts: { status?: number; base?: string; causeName?: string; timedOut?: boolean } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.base = opts.base;
    this.causeName = opts.causeName;
    this.timedOut = opts.timedOut ?? false;
  }

  /** Short technical line for debugging, e.g. "AbortError: Aborted" or "HTTP 400: ...". */
  get detail(): string {
    if (this.status !== undefined) return `HTTP ${this.status}: ${this.message}`;
    return `${this.causeName ?? "Error"}: ${this.message}`;
  }

  /** True when we never got a response from any candidate (offline/typo). */
  get isNetworkError(): boolean {
    return this.status === undefined;
  }
}

export type UploadErrorKind = "network" | "timeout" | "http" | "client";

/** Failure of the photo upload: { kind, status, message }. */
export class UploadError extends Error {
  readonly kind: UploadErrorKind;
  readonly status?: number;

  constructor(kind: UploadErrorKind, message: string, status?: number) {
    super(message);
    this.name = "UploadError";
    this.kind = kind;
    this.status = status;
  }

  /** Technical line, e.g. "HTTP 400: Photo was taken on ..." or "network: request failed". */
  get detail(): string {
    return this.kind === "http" ? `HTTP ${this.status}: ${this.message}` : `${this.kind}: ${this.message}`;
  }
}

/** Honest, specific wording for each failure; `detail` is the technical line. */
export function describeUploadError(err: UploadError): { message: string; detail: string } {
  let message: string;
  switch (err.kind) {
    case "network":
      message = "Couldn't reach your server";
      break;
    case "timeout":
      message = "The upload took too long";
      break;
    case "http":
      message = err.status === 401 ? "Token mismatch, check Settings" : err.message;
      break;
    default:
      message = "The app couldn't prepare the photo";
  }
  return { message, detail: err.detail };
}

type RequestOptions = {
  json?: unknown;
  timeoutMs?: number;
};

async function detailFrom(response: Response): Promise<string> {
  try {
    const text = await response.text();
    if (text) {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
    }
  } catch {
    /* fall through to the status text */
  }
  return `Request failed (HTTP ${response.status})`;
}

function makeInit(method: string, token: string, options: RequestOptions): RequestInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token.trim()) headers.Authorization = `Bearer ${token.trim()}`;
  // JSON requests only (the photo upload uses XHR and sets no Content-Type).
  if (options.json !== undefined) headers["Content-Type"] = "application/json";

  const init: RequestInit = { method, headers };
  if (options.json !== undefined) init.body = JSON.stringify(options.json);
  return init;
}

export class TouchGrassClient {
  private lastWorking: string | null = null;
  private memoryLoaded = false;

  constructor(private getSettings: () => AppSettings) {}

  /** The base URL for plain resource URLs (images are fetched by useAuthedImage, which adds the token). */
  currentBase(): string {
    const s = this.getSettings();
    return trimBase(this.lastWorking || s.server1) || trimBase(s.server2);
  }

  /** Full URL for a proof image. Callers must attach the bearer token. */
  imageUrl(submissionId: number, thumb = false): string {
    const base = this.currentBase();
    return `${base}/images/${submissionId}${thumb ? "?thumb=1" : ""}`;
  }

  private async candidateBases(
    ignoreMemory = false,
    settings = this.getSettings(),
  ): Promise<string[]> {
    if (!ignoreMemory && !this.memoryLoaded) {
      try {
        this.lastWorking = await AsyncStorage.getItem(STORAGE.lastBaseUrl);
      } catch {
        /* storage unavailable - fall back to configured URLs */
      }
      this.memoryLoaded = true;
    }

    const s = settings;
    const seen = new Set<string>();
    const add = (raw: string | null | undefined) => {
      const base = trimBase(raw);
      if (base && !seen.has(base)) seen.add(base);
    };
    if (!ignoreMemory) add(this.lastWorking);
    add(s.server1);
    add(s.server2);
    return [...seen];
  }

  private async remember(base: string): Promise<void> {
    this.lastWorking = base;
    try {
      await AsyncStorage.setItem(STORAGE.lastBaseUrl, base);
    } catch {
      /* not persisted - the in-memory value still stands */
    }
  }

  /** One attempt against one base URL; throws ApiError on any failure. */
  private async fetchResponse(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      // Fresh init per attempt so the signal is never shared between requests.
      const response = await fetch(url, { ...init, signal: controller.signal });
      if (!response.ok) {
        throw new ApiError(await detailFrom(response), { status: response.status });
      }
      return response;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      const e = err instanceof Error ? err : new Error(String(err));
      throw new ApiError(e.message, { causeName: e.name, timedOut });
    } finally {
      clearTimeout(timer);
    }
  }

  /** Decodes a (JSON) response body. */
  private async fetchOnce<T>(url: string, init: RequestInit, timeoutMs: number): Promise<T> {
    const response = await this.fetchResponse(url, init, timeoutMs);
    const text = await response.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      return text as unknown as T;
    }
  }

  /**
   * Pick the server to talk to. Uses the last working base when it is still
   * one of the configured URLs; otherwise probes /health on each candidate
   * (5 s each - the only place the short timeout applies).
   */
  private async selectBase(): Promise<string> {
    const candidates = await this.candidateBases();
    const s = this.getSettings();
    const configured = [trimBase(s.server1), trimBase(s.server2)];
    if (this.lastWorking && configured.includes(trimBase(this.lastWorking))) {
      return trimBase(this.lastWorking);
    }
    const failures: ApiError[] = [];
    for (const base of candidates) {
      try {
        await this.fetchOnce<Health>(`${base}/health`, makeInit("GET", s.token, {}), PROBE_TIMEOUT_MS);
        await this.remember(base);
        return base;
      } catch (err) {
        failures.push(err instanceof ApiError ? err : new ApiError(String(err)));
      }
    }
    throw failures[0] ?? new ApiError("No server URL is configured.");
  }

  /**
   * Reachability check for the sync engine: /health on each configured URL
   * (last working first, 5 s each). Remembers the base that answers.
   */
  async probe(): Promise<boolean> {
    const s = this.getSettings();
    for (const base of await this.candidateBases()) {
      try {
        const health = await this.fetchOnce<Health>(`${base}/health`, makeInit("GET", s.token, {}), PROBE_TIMEOUT_MS);
        if (health && health.ok) {
          await this.remember(base);
          return true;
        }
      } catch {
        /* try the next URL */
      }
    }
    return false;
  }

  private async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS;
    const attempt = async (base: string) =>
      this.fetchOnce<T>(`${base}${path}`, makeInit(method, this.getSettings().token, options), timeoutMs);

    const base = await this.selectBase();
    try {
      return await attempt(base);
    } catch (err) {
      // HTTP errors are final. Only a network-level failure re-selects once
      // (safe for uploads too: client_submission_id is idempotent).
      if (!(err instanceof ApiError) || !err.isNetworkError) throw err;
      this.lastWorking = null;
      const next = await this.selectBase().catch(() => null);
      if (!next || next === base) throw err;
      return attempt(next);
    }
  }

  // --- endpoints ------------------------------------------------------------

  health(): Promise<Health> {
    return this.request<Health>("GET", "/health");
  }

  tasks(from?: string, days?: number): Promise<TasksResponse> {
    const params = new URLSearchParams();
    if (from) params.set("from", from);
    if (days !== undefined) params.set("days", String(days));
    const query = params.size ? `?${params.toString()}` : "";
    return this.request<TasksResponse>("GET", `/tasks${query}`);
  }

  reroll(taskId: number): Promise<RerollResponse> {
    return this.request<RerollResponse>("POST", `/tasks/${taskId}/reroll`);
  }

  /** Base for the upload: last working URL, else a configured one. No probing, no short timeout. */
  private async uploadBase(): Promise<string> {
    await this.candidateBases(); // loads the remembered base from storage once
    const s = this.getSettings();
    const configured = [trimBase(s.server1), trimBase(s.server2)].filter(Boolean);
    const last = trimBase(this.lastWorking);
    if (last && configured.includes(last)) return last;
    if (configured[0]) return configured[0];
    throw new UploadError("client", "No server URL is configured.");
  }

  /**
   * Upload the proof photo with XMLHttpRequest. Expo's fetch rejects React
   * Native style { uri, name, type } FormData parts, XHR accepts them.
   * Rejects with an UploadError (kind: network | timeout | http | client).
   */
  async submitPhoto(input: SubmitInput): Promise<SubmissionCreate> {
    const base = await this.uploadBase();
    const token = this.getSettings().token.trim();

    const form = new FormData();
    form.append("task_id", String(input.taskId));
    form.append("client_submission_id", input.clientSubmissionId);
    form.append("captured_at", input.capturedAt);
    if (input.note) form.append("note", input.note);
    if (input.lat !== undefined) form.append("lat", String(input.lat));
    if (input.lon !== undefined) form.append("lon", String(input.lon));
    // React Native file part (not a web Blob), hence the cast.
    form.append("image", { uri: input.image.uri, name: "photo.jpg", type: "image/jpeg" } as any);

    const result = await new Promise<SubmissionCreate>((resolve, reject) => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${base}/submissions`);
        xhr.setRequestHeader("Accept", "application/json");
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        // No Content-Type: React Native adds the multipart boundary itself.
        xhr.timeout = UPLOAD_TIMEOUT_MS;
        xhr.responseType = "text";
        xhr.onload = () => {
          const text = String(xhr.responseText ?? "");
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(text) as SubmissionCreate);
            } catch {
              reject(new UploadError("client", "The server sent an unreadable reply.", xhr.status));
            }
            return;
          }
          let message = `HTTP ${xhr.status}`;
          try {
            const parsed = JSON.parse(text);
            if (parsed && typeof parsed.detail === "string") message = parsed.detail;
          } catch {
            /* keep the status text */
          }
          reject(new UploadError("http", message, xhr.status));
        };
        xhr.onerror = () => reject(new UploadError("network", "request failed"));
        xhr.onabort = () => reject(new UploadError("network", "request aborted"));
        xhr.ontimeout = () => reject(new UploadError("timeout", `no reply after ${UPLOAD_TIMEOUT_MS / 1000}s`));
        xhr.send(form);
      } catch (err) {
        reject(new UploadError("client", err instanceof Error ? err.message : String(err)));
      }
    });
    await this.remember(base);
    return result;
  }

  getSubmission(id: number): Promise<Submission> {
    return this.request<Submission>("GET", `/submissions/${id}`);
  }

  honor(id: number): Promise<HonorResponse> {
    return this.request<HonorResponse>("POST", `/submissions/${id}/honor`);
  }

  stats(): Promise<Stats> {
    return this.request<Stats>("GET", "/stats");
  }

  history(limit = 50, offset = 0): Promise<HistoryResponse> {
    return this.request<HistoryResponse>(
      "GET",
      `/history?limit=${limit}&offset=${offset}`,
    );
  }

  getPreferences(): Promise<Preferences> {
    return this.request<Preferences>("GET", "/preferences");
  }

  putPreferences(patch: PreferencesPatch): Promise<Preferences> {
    return this.request<Preferences>("PUT", "/preferences", { json: patch });
  }

  resetPreferences(): Promise<Preferences> {
    return this.request<Preferences>("POST", "/preferences/reset");
  }

  /**
   * Test the configured URLs in order (ignoring the remembered one) and
   * report the model name on the first reachable server.
   */
  async testConnection(
    override?: Partial<AppSettings>,
  ): Promise<{ ok: true; model: string; base: string } | { ok: false; message: string }> {
    const s = { ...this.getSettings(), ...(override ?? {}) };
    const init = makeInit("GET", s.token, {});
    const failures: ApiError[] = [];

    for (const base of await this.candidateBases(true, s)) {
      try {
        const health = await this.fetchOnce<Health>(`${base}/health`, init, PROBE_TIMEOUT_MS);
        if (health && health.ok) return { ok: true, model: health.model, base };
        return { ok: false, message: "The server answered but reported itself unhealthy." };
      } catch (err) {
        failures.push(err instanceof ApiError ? err : new ApiError(String(err)));
      }
    }

    const answered = failures.find((f) => !f.isNetworkError) ?? failures[0];
    return { ok: false, message: answered?.message ?? "No server URL is configured." };
  }
}