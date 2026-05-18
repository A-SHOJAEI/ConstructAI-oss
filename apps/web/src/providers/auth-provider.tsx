"use client";

import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useProjectStore } from "@/stores/project-store";

interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  org_id: string;
}

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getCsrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.split("; ").find((c) => c.startsWith("csrf_token="));
  return match ? match.split("=").slice(1).join("=") : "";
}

// SECURITY [H-07]: All token storage removed from sessionStorage. Auth relies
// exclusively on httpOnly cookies (credentials: "include").
async function fetchMe(): Promise<User | null> {
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/me`, {
      credentials: "include",
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// SECURITY [H-07]: sessionStorage fallback removed. Refresh relies on httpOnly cookies only.
async function tryRefresh(): Promise<boolean> {
  try {
    const csrfToken = getCsrfToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

    const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({}),
    });
    if (!res.ok) return false;
    // Tokens are set via httpOnly Set-Cookie headers by the backend.
    return true;
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // SECURITY [H-07]: sessionStorage token removal not needed; tokens are httpOnly cookies.
  const clearAuth = useCallback(() => {
    setUser(null);
    // Clear project store to prevent stale data from previous user session
    useProjectStore.getState().clearProject();
  }, []);

  // Initialize auth state by calling /auth/me
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // First try /auth/me with existing httpOnly cookie
      let me = await fetchMe();
      if (!me) {
        // If that fails, try refreshing
        const refreshed = await tryRefresh();
        if (refreshed) {
          me = await fetchMe();
        }
      }
      if (!cancelled) {
        setUser(me);
        setIsLoading(false);
        // Redirect removed — middleware already guards protected routes and
        // redirects unauthenticated users to /login. The client-side redirect
        // caused a visual flash of dashboard content before navigation.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-refresh timer — refresh tokens before expiry
  useEffect(() => {
    const interval = setInterval(
      async () => {
        if (!user) return;
        const refreshed = await tryRefresh();
        if (!refreshed) {
          clearAuth();
          router.push("/login");
        }
      },
      4 * 60 * 1000,
    ); // Refresh every 4 minutes (before 5-min default expiry)
    return () => clearInterval(interval);
  }, [clearAuth, router, user]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Login failed" }));
      throw new Error(err.detail || "Login failed");
    }
    // SECURITY [H-07]: Tokens are set via httpOnly cookies by the backend.
    // No sessionStorage storage needed.
    // Fetch user profile from /me (authoritative source)
    const me = await fetchMe();
    setUser(me);
  }, []);

  // SECURITY [H-07]: Logout uses httpOnly cookies only, no Bearer token from sessionStorage.
  const logout = useCallback(async () => {
    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {};
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

      await fetch(`${API_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers,
        credentials: "include",
      });
    } catch {
      // Logout failure is non-critical
    }
    clearAuth();
    router.push("/login");
  }, [clearAuth, router]);

  const value = useMemo(
    () => ({ user, isLoading, login, logout }),
    [user, isLoading, login, logout],
  );

  return (
    <AuthContext.Provider value={value}>
      {isLoading ? (
        <div
          className="flex items-center justify-center min-h-screen"
          role="status"
          aria-busy="true"
          aria-label="Loading application"
        >
          <div
            className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900 dark:border-gray-100"
            aria-hidden="true"
          />
          <span className="sr-only">Loading application, please wait...</span>
        </div>
      ) : (
        children
      )}
    </AuthContext.Provider>
  );
}
