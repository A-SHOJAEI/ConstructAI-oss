import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { AuthContext } from "@/providers/auth-provider";

// Mock next/navigation (AuthProvider uses useRouter)
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/test",
  useSearchParams: () => new URLSearchParams(),
}));

// Import after mocks
import { useAuth } from "@/hooks/use-auth";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createWrapper(contextValue: {
  user: { id: string; email: string; full_name: string; role: string; org_id: string } | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(AuthContext.Provider, { value: contextValue }, children);
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useAuth", () => {
  const mockLogin = vi.fn();
  const mockLogout = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    mockLogin.mockReset();
    mockLogout.mockReset();
  });

  it("returns user when authenticated", () => {
    const wrapper = createWrapper({
      user: {
        id: "user-1",
        email: "test@example.com",
        full_name: "Test User",
        role: "admin",
        org_id: "org-1",
      },
      isLoading: false,
      login: mockLogin,
      logout: mockLogout,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.user).toEqual({
      id: "user-1",
      email: "test@example.com",
      full_name: "Test User",
      role: "admin",
      org_id: "org-1",
    });
    expect(result.current.isLoading).toBe(false);
  });

  it("returns null user when not authenticated", () => {
    const wrapper = createWrapper({
      user: null,
      isLoading: false,
      login: mockLogin,
      logout: mockLogout,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.user).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it("returns isLoading true during authentication check", () => {
    const wrapper = createWrapper({
      user: null,
      isLoading: true,
      login: mockLogin,
      logout: mockLogout,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.user).toBeNull();
    expect(result.current.isLoading).toBe(true);
  });

  it("throws when used outside AuthProvider", () => {
    // renderHook without a wrapper that provides AuthContext
    expect(() => {
      renderHook(() => useAuth());
    }).toThrow("useAuth must be used within an AuthProvider");
  });

  it("exposes login function from context", () => {
    const wrapper = createWrapper({
      user: null,
      isLoading: false,
      login: mockLogin,
      logout: mockLogout,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.login).toBe(mockLogin);
  });

  it("exposes logout function from context", () => {
    const wrapper = createWrapper({
      user: {
        id: "user-1",
        email: "test@example.com",
        full_name: "Test User",
        role: "admin",
        org_id: "org-1",
      },
      isLoading: false,
      login: mockLogin,
      logout: mockLogout,
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.logout).toBe(mockLogout);

    // Call logout and verify it was invoked
    result.current.logout();
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });
});
