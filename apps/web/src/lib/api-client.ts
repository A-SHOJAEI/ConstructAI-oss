// L-10: Validate BASE_URL at module load. In prod we reject non-HTTPS;
// in dev we allow http://localhost for convenience.
function resolveBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  try {
    const parsed = new URL(raw);
    if (
      process.env.NODE_ENV === "production" &&
      parsed.protocol !== "https:" &&
      parsed.hostname !== "localhost" &&
      parsed.hostname !== "127.0.0.1"
    ) {
      throw new Error("NEXT_PUBLIC_API_URL must use HTTPS in production");
    }
    return raw;
  } catch (err) {
    throw new Error(
      `Invalid NEXT_PUBLIC_API_URL ${JSON.stringify(raw)}: ${(err as Error).message}`,
    );
  }
}

const BASE_URL = resolveBaseUrl();

// H-14: default request timeout (ms). Individual callers can override via the
// `timeoutMs` option. Without this a hung server can tie up a tab forever.
const DEFAULT_TIMEOUT_MS = 30_000;

// H-15: methods that are safe to auto-retry after a token refresh. POST /
// PUT / PATCH / DELETE are NOT in this list because a silent retry after
// refresh can double-apply a mutation (two RFIs, two pay apps, etc.).
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class NetworkError extends Error {
  constructor(message: string = "Network error — please check your connection and try again.") {
    super(message);
    this.name = "NetworkError";
  }
}

export class TimeoutError extends Error {
  constructor(message: string = "Request timed out. Please try again.") {
    super(message);
    this.name = "TimeoutError";
  }
}

/**
 * Thrown when a mutating request (POST/PUT/PATCH/DELETE) returns 401.
 *
 * The api-client will refresh the auth token but must NOT silently re-send
 * the mutation — the caller needs to decide whether to retry (and the user
 * needs to see that something went wrong). Catch this and prompt/retry at
 * the call site.
 */
export class AuthRefreshedError extends Error {
  constructor(message: string = "Session refreshed — please retry your action.") {
    super(message);
    this.name = "AuthRefreshedError";
  }
}

function getCsrfHeader(): Record<string, string> {
  if (typeof document === "undefined") return {};
  const match = document.cookie.split("; ").find((c) => c.startsWith("csrf_token="));
  if (!match) return {};
  return { "X-CSRF-Token": match.split("=").slice(1).join("=") };
}

let refreshPromise: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
    try {
      const res = await fetch(`${BASE_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getCsrfHeader() },
        credentials: "include",
        signal: controller.signal,
      });
      return res.ok;
    } finally {
      clearTimeout(timeoutId);
    }
  } catch {
    return false;
  }
}

async function handleUnauthorized(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

interface RequestOptions extends RequestInit {
  /** Override the 30s default request timeout. Set to 0 to disable. */
  timeoutMs?: number;
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  // Compose with any caller-supplied signal so external aborts still work.
  const controller = new AbortController();
  const callerSignal = init.signal;
  const onCallerAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort(callerSignal.reason);
    else callerSignal.addEventListener("abort", onCallerAbort, { once: true });
  }
  const timeoutId = timeoutMs > 0 ? setTimeout(() => controller.abort("timeout"), timeoutMs) : null;
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    if (callerSignal) callerSignal.removeEventListener("abort", onCallerAbort);
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const method = (options.method || "GET").toUpperCase();
  const isMutation = !IDEMPOTENT_METHODS.has(method);
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(isMutation ? getCsrfHeader() : {}),
    ...(options.headers as Record<string, string>),
  };

  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  // Strip custom fields before forwarding to fetch.
  const { timeoutMs: _unused, ...fetchOptions } = options;
  void _unused;

  let response: Response;
  try {
    response = await fetchWithTimeout(
      url,
      { ...fetchOptions, headers, credentials: "include" },
      timeoutMs,
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new TimeoutError();
    }
    if (error instanceof TypeError) {
      throw new NetworkError();
    }
    throw error;
  }

  // 401 handling: refresh tokens, then retry ONLY idempotent requests.
  // Mutating requests must not auto-retry — a retry after refresh can
  // double-apply a side effect the caller didn't explicitly authorize.
  if (response.status === 401) {
    const refreshed = await handleUnauthorized();
    if (refreshed) {
      if (!IDEMPOTENT_METHODS.has(method)) {
        // Refresh succeeded, but we won't re-send the mutation automatically.
        // Surface a typed error so the UI can prompt the user to retry.
        throw new AuthRefreshedError();
      }
      try {
        response = await fetchWithTimeout(
          url,
          { ...fetchOptions, headers, credentials: "include" },
          timeoutMs,
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          throw new TimeoutError();
        }
        if (error instanceof TypeError) {
          throw new NetworkError();
        }
        throw error;
      }
    }
  }

  if (!response.ok) {
    let message = "Request failed";
    try {
      const err = await response.json();
      message = err.detail || err.message || message;
    } catch {
      if (response.status === 403) message = "You don't have permission for this action";
      else if (response.status === 404) message = "The requested resource was not found";
      else if (response.status === 422) message = "Invalid data submitted";
      else if (response.status === 429) message = "Too many requests. Please wait and try again";
      else if (response.status >= 500) message = "Server error. Please try again later";
    }
    throw new Error(message);
  }

  // Handle 204 No Content
  if (response.status === 204) return undefined as T;

  return response.json();
}

export const apiClient = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, {
      ...options,
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "DELETE" }),
  upload: <T>(path: string, formData: FormData, options?: RequestOptions) =>
    request<T>(path, {
      ...options,
      method: "POST",
      body: formData,
    }),
};
