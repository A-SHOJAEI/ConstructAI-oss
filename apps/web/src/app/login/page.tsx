"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

/**
 * Validates that a redirect path is a safe relative path (starts with "/"
 * and does not start with "//" to prevent open-redirect attacks).
 */
function getSafeRedirect(redirect: string | null): string {
  if (
    redirect &&
    redirect.startsWith("/") &&
    !redirect.startsWith("//") &&
    !redirect.includes("://")
  ) {
    return redirect;
  }
  return "/projects";
}

function getCsrfToken(): string {
  const match = document.cookie.match(/csrf_token=([^;]+)/);
  return match ? match[1] : "";
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = getSafeRedirect(searchParams.get("redirect"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showMfa, setShowMfa] = useState(false);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaToken, setMfaToken] = useState("");

  // RT6-AUTH-02: SSO callback now returns an opaque code instead of tokens.
  // Exchange the code for tokens via a secure POST endpoint.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash;
    if (!hash) return;

    const params = new URLSearchParams(hash.substring(1));
    const sso = params.get("sso");

    if (sso === "success") {
      const code = params.get("code");
      if (!code) return;

      // Clear the fragment immediately to prevent leakage
      window.history.replaceState(null, "", "/login");

      // Exchange the one-time code for tokens
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      fetch(`${apiUrl}/api/v1/auth/sso/exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
        credentials: "include",
        body: JSON.stringify({ code }),
      })
        .then((res) => {
          if (!res.ok) throw new Error("SSO code exchange failed");
          return res.json();
        })
        .then(() => {
          // Tokens are set as httpOnly cookies by the backend
          router.push(redirectTo);
        })
        .catch(() => {
          setError("SSO sign-in failed. Please try again.");
        });
    } else if (sso === "mfa_required") {
      // SSO user with MFA enabled — exchange the opaque code for the real MFA token
      const mfaAuthCode = params.get("mfa_token");
      if (mfaAuthCode) {
        window.history.replaceState(null, "", "/login");
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        fetch(`${apiUrl}/api/v1/auth/sso/exchange`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
          credentials: "include",
          body: JSON.stringify({ code: mfaAuthCode }),
        })
          .then((res) => {
            if (!res.ok) throw new Error("SSO MFA code exchange failed");
            return res.json();
          })
          .then((data) => {
            if (data.mfa_required && data.mfa_token) {
              setMfaToken(data.mfa_token);
              setShowMfa(true);
            } else {
              setError("SSO sign-in failed. Unexpected response.");
            }
          })
          .catch(() => {
            setError("SSO sign-in failed. Please try again.");
          });
      }
    } else if (sso === "email_unverified") {
      // RT6-AUTH-07: SSO user with unverified email
      window.history.replaceState(null, "", "/login");
      setError(
        "Email verification required. Please check your email for a verification link before signing in via SSO.",
      );
    }
  }, [router, redirectTo]);

  async function handleSSO(provider: string) {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const allowedProviders = ["google", "microsoft"];
    if (!allowedProviders.includes(provider)) {
      setError("Unknown SSO provider");
      return;
    }
    try {
      const res = await fetch(`${apiUrl}/api/v1/auth/sso/${provider}/authorize`, {
        credentials: "include",
      });
      const data = await res.json();
      // RT5-H01 / L-11: Validate authorize_url origin before redirecting.
      // Allowlist is config-driven so additional IdPs (Okta, Auth0) can be
      // onboarded without a code change. Falls back to Google/Microsoft.
      if (data.authorize_url) {
        try {
          const parsed = new URL(data.authorize_url);
          const envList = process.env.NEXT_PUBLIC_SSO_ALLOWED_HOSTS;
          const allowedHosts = envList
            ? envList
                .split(",")
                .map((h) => h.trim())
                .filter(Boolean)
            : ["accounts.google.com", "login.microsoftonline.com"];
          if (!allowedHosts.includes(parsed.hostname)) {
            setError("Invalid SSO redirect URL");
            return;
          }
          window.location.href = data.authorize_url;
        } catch {
          setError("Invalid SSO redirect URL");
        }
      }
    } catch {
      setError(`Failed to initiate ${provider} sign-in`);
    }
  }

  async function handleMfaSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    try {
      const res = await fetch(`${apiUrl}/api/v1/auth/mfa/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
        credentials: "include",
        body: JSON.stringify({ mfa_token: mfaToken, code: mfaCode }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "MFA verification failed" }));
        throw new Error(err.detail || "MFA verification failed");
      }
      // Tokens are set as httpOnly cookies by the backend
      setMfaToken("");
      router.push(redirectTo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "MFA verification failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiUrl}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Login failed" }));
        throw new Error(err.detail || "Login failed");
      }
      const data = await res.json().catch(() => ({}));
      if (data.mfa_required && data.mfa_token) {
        setMfaToken(data.mfa_token);
        setShowMfa(true);
        return;
      }
      // Auth relies exclusively on httpOnly cookies set by the backend.
      // No tokens are exposed in the JSON response body.
      router.push(redirectTo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-md">
        <h1 className="mb-8 text-center text-3xl font-bold dark:text-white">Sign In</h1>
        {error && (
          <div
            id="login-error"
            role="alert"
            className="mb-4 rounded-lg bg-red-50 dark:bg-red-900/20 p-4 text-red-700 dark:text-red-400"
          >
            {error}
          </div>
        )}
        {showMfa ? (
          <form onSubmit={handleMfaSubmit} className="space-y-6">
            <p className="text-sm text-gray-600 dark:text-gray-400">
              Enter the 6-digit code from your authenticator app.
            </p>
            <div>
              <label
                htmlFor="mfa-code"
                className="block text-sm font-medium text-gray-700 dark:text-gray-300"
              >
                MFA Code
              </label>
              <input
                id="mfa-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                required
                autoFocus
                className="mt-1 block w-full rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-800 dark:text-white px-3 py-2 shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary text-center text-2xl tracking-widest"
              />
            </div>
            <button
              type="submit"
              disabled={loading || mfaCode.length < 6}
              className="w-full rounded-lg bg-primary px-4 py-2 text-white font-semibold hover:bg-primary-dark disabled:opacity-50 transition-colors"
            >
              {loading ? "Verifying..." : "Verify"}
            </button>
          </form>
        ) : (
          // noValidate: HTML5 email validation rejects underscores in
          // the domain part (e.g. user@demo_session_01.test), blocking
          // the submit event from ever firing. RFC 6531 permits
          // underscores; HTML5 doesn't. Server-side validation handles
          // email shape correctly.
          <form onSubmit={handleSubmit} noValidate className="space-y-6">
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700 dark:text-gray-300"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                aria-invalid={!!error}
                aria-describedby={error ? "login-error" : undefined}
                className="mt-1 block w-full rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-800 dark:text-white px-3 py-2 shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700 dark:text-gray-300"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                aria-invalid={!!error}
                aria-describedby={error ? "login-error" : undefined}
                className="mt-1 block w-full rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-800 dark:text-white px-3 py-2 shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-primary px-4 py-2 text-white font-semibold hover:bg-primary-dark disabled:opacity-50 transition-colors"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>
        )}
        {/* SSO Providers */}
        <div className="mt-6">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300 dark:border-gray-600" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-white px-2 text-gray-500 dark:bg-gray-900 dark:text-gray-400">
                Or continue with
              </span>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => handleSSO("google")}
              className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24">
                <path
                  fill="#4285F4"
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                />
                <path
                  fill="#34A853"
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                />
                <path
                  fill="#FBBC05"
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                />
                <path
                  fill="#EA4335"
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                />
              </svg>
              Google
            </button>
            <button
              type="button"
              onClick={() => handleSSO("microsoft")}
              className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <svg className="h-4 w-4" viewBox="0 0 21 21">
                <rect x="1" y="1" width="9" height="9" fill="#f25022" />
                <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
                <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
                <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
              </svg>
              Microsoft
            </button>
          </div>
        </div>

        <p className="mt-4 text-center text-sm text-gray-500 dark:text-gray-400">
          <Link href="/forgot-password" className="font-medium text-primary hover:underline">
            Forgot your password?
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center p-8">
          <div className="text-center text-gray-400">Loading...</div>
        </main>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
