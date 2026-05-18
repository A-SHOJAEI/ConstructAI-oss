import { expect, test, describe, vi, beforeEach } from "vitest";

describe("apiClient", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    sessionStorage.clear();
    localStorage.clear();
  });

  test("GET request uses credentials: include for cookie auth", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: "ok" }),
    });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.get("/api/v1/test");
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.credentials).toBe("include");
    // NO Authorization header — auth is via httpOnly cookies
    expect(options.headers?.Authorization).toBeUndefined();
  });

  test("POST request includes CSRF header", async () => {
    // Set CSRF cookie
    Object.defineProperty(document, "cookie", {
      writable: true,
      value: "csrf_token=test-csrf-123",
      configurable: true,
    });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: "created" }),
    });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.post("/api/v1/items", { name: "test" });
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.headers["X-CSRF-Token"]).toBe("test-csrf-123");
    expect(options.credentials).toBe("include");
  });

  test("GET request does NOT include CSRF header", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: "ok" }),
    });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.get("/api/v1/test");
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.headers?.["X-CSRF-Token"]).toBeUndefined();
  });

  test("POST request sends JSON body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: "123" }),
    });

    const { apiClient } = await import("@/lib/api-client");
    await apiClient.post("/api/v1/items", { name: "test" });

    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ name: "test" });
  });

  test("throws error on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Bad request" }),
    });

    const { apiClient } = await import("@/lib/api-client");
    await expect(apiClient.get("/api/v1/bad")).rejects.toThrow("Bad request");
  });

  test("handles 204 No Content", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => null,
    });

    const { apiClient } = await import("@/lib/api-client");
    const result = await apiClient.delete("/api/v1/items/123");
    expect(result).toBeUndefined();
  });

  test("retries on 401 by calling refresh endpoint with credentials", async () => {
    let callCount = 0;
    global.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/auth/refresh")) {
        return { ok: true, json: async () => ({ message: "refreshed" }) };
      }
      callCount++;
      if (callCount === 1) {
        return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
      }
      return { ok: true, status: 200, json: async () => ({ data: "ok" }) };
    });
    const { apiClient } = await import("@/lib/api-client");
    const result = await apiClient.get("/api/v1/protected");
    expect(result).toEqual({ data: "ok" });
    // Verify refresh was called with credentials: include (cookie-based)
    const refreshCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      (c: unknown[]) => typeof c[0] === "string" && c[0].includes("/auth/refresh"),
    );
    expect(refreshCall).toBeDefined();
    expect(refreshCall![1].credentials).toBe("include");
    // NO tokens stored in sessionStorage
    expect(sessionStorage.getItem("access_token")).toBeNull();
  });

  test("DELETE request uses correct method", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.delete("/api/v1/items/123");
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.method).toBe("DELETE");
    expect(options.credentials).toBe("include");
  });

  test("PATCH request sends body and correct method", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: "patched" }),
    });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.patch("/api/v1/items/123", { name: "updated" });
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ name: "updated" });
  });

  test("no tokens stored in sessionStorage or localStorage after any operation", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: "ok" }),
    });
    const { apiClient } = await import("@/lib/api-client");
    await apiClient.get("/api/v1/test");
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
  });
});
