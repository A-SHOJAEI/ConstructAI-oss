/**
 * M-62: Verify the api-client does NOT silently retry mutating requests
 * after a 401 → refresh cycle. Retrying a POST after refresh would
 * double-apply the mutation (create two RFIs, two pay apps, etc.). The
 * client must surface AuthRefreshedError so the UI can prompt the user.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

describe("api-client method-gated 401 retry (M-62)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    // Clear the inlined CSRF cookie between tests.
    Object.defineProperty(document, "cookie", {
      writable: true,
      value: "csrf_token=test-csrf",
      configurable: true,
    });
  });

  test("GET retries after 401 → refresh", async () => {
    const fetchMock = vi
      .fn()
      // first call: 401
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      // refresh: 200
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) })
      // retried GET: 200
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ data: "ok" }) });
    global.fetch = fetchMock as unknown as typeof fetch;

    const { apiClient } = await import("@/lib/api-client");
    await apiClient.get("/api/v1/foo");
    // three calls: original GET, refresh, retry GET.
    expect(fetchMock.mock.calls.length).toBe(3);
  });

  test("POST does NOT retry after 401 → refresh; throws AuthRefreshedError instead", async () => {
    const fetchMock = vi
      .fn()
      // POST: 401
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      // refresh: 200
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) });
    // NO third mock — a call here would be the forbidden retry.
    global.fetch = fetchMock as unknown as typeof fetch;

    const { apiClient, AuthRefreshedError } = await import("@/lib/api-client");

    await expect(apiClient.post("/api/v1/items", { name: "x" })).rejects.toBeInstanceOf(
      AuthRefreshedError,
    );
    // Two calls only — original POST + refresh. Never a retry.
    expect(fetchMock.mock.calls.length).toBe(2);
  });

  test.each([["put"], ["patch"], ["delete"]] as const)(
    "%s also does not retry after refresh",
    async (method) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
        .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) });
      global.fetch = fetchMock as unknown as typeof fetch;

      const { apiClient, AuthRefreshedError } = await import("@/lib/api-client");

      const call =
        method === "delete"
          ? apiClient.delete("/api/v1/items/1")
          : (apiClient as unknown as Record<string, (p: string, b: unknown) => Promise<unknown>>)[
              method
            ]("/api/v1/items/1", { foo: "bar" });

      await expect(call).rejects.toBeInstanceOf(AuthRefreshedError);
      expect(fetchMock.mock.calls.length).toBe(2);
    },
  );

  test("times out after DEFAULT_TIMEOUT when fetch hangs", async () => {
    // fetch that never resolves — should be aborted by the timeout.
    global.fetch = vi.fn(
      (_url, opts: RequestInit | undefined) =>
        new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener("abort", () => {
            const err = new DOMException("aborted", "AbortError");
            reject(err);
          });
        }),
    ) as unknown as typeof fetch;

    const { apiClient, TimeoutError } = await import("@/lib/api-client");

    await expect(apiClient.get("/api/v1/foo", { timeoutMs: 20 })).rejects.toBeInstanceOf(
      TimeoutError,
    );
  });
});
