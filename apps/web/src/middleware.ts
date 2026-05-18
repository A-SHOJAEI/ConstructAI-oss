import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/", "/login", "/forgot-password", "/reset-password"];

function isTokenStructurallyValid(token: string): boolean {
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  try {
    // Base64url-safe decoding: replace URL-safe chars with standard Base64 chars
    const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(padded));
    if (typeof payload.exp === "number") {
      // Reject if token is expired (compare in seconds)
      return payload.exp > Date.now() / 1000;
    }
    // If no exp claim, accept (backend will validate fully)
    return true;
  } catch {
    return false;
  }
}

// Warn if production API URL is missing or still pointing at localhost
if (
  process.env.NODE_ENV === "production" &&
  (!process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_URL.includes("localhost"))
) {
  console.warn(
    "[middleware] NEXT_PUBLIC_API_URL is not set or contains 'localhost' in production. " +
      "CSP connect-src may be misconfigured.",
  );
}

/**
 * Resolve the API URL for CSP connect-src. In production, strip localhost
 * URLs so the bundle never whitelists localhost in the Content-Security-Policy.
 */
// Removed resolveApiUrlForCSP — nginx now owns CSP for the demo.

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths and static assets
  if (
    PUBLIC_PATHS.includes(pathname) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.match(/\.(css|js|json|ico|png|jpg|jpeg|gif|svg|woff2?|ttf|eot|map)$/)
  ) {
    return NextResponse.next();
  }

  // Check for access token in cookie or header
  const token =
    request.cookies.get("access_token")?.value ||
    request.headers.get("authorization")?.replace("Bearer ", "");

  // If access token is missing or expired but a refresh token cookie exists,
  // allow the page to load so the client-side AuthProvider can attempt a
  // token refresh via /auth/refresh. Without this, a hard refresh after
  // access token expiry would bounce the user to /login even though their
  // refresh cookie is still valid.
  const hasRefreshToken = !!request.cookies.get("refresh_token")?.value;

  if (!token || !isTokenStructurallyValid(token)) {
    if (hasRefreshToken) {
      // Let the page load — AuthProvider will call /auth/refresh
      const response = NextResponse.next();
      return response;
    }
    const loginUrl = new URL("/login", request.url);
    // Only allow safe relative redirects: must start with "/" and must not
    // contain "://" or start with "//" to prevent open-redirect attacks.
    if (pathname.startsWith("/") && !pathname.startsWith("//") && !pathname.includes("://")) {
      loginUrl.searchParams.set("redirect", pathname);
    }
    return NextResponse.redirect(loginUrl);
  }

  // CSP is owned by the front-line nginx reverse proxy in the demo
  // deployment — it generates a per-request nonce, injects it onto every
  // <script> tag via sub_filter, and emits a matching Content-Security-
  // Policy header. The Next.js middleware here only sets the non-CSP
  // security headers (X-Content-Type-Options, X-Frame-Options, etc.).
  //
  // We keep an internal x-nonce header for any future Next.js-side
  // injection (e.g. <Script nonce={await headers().then(h => h.get('x-nonce'))} />)
  // but do NOT set Content-Security-Policy from here. nginx strips any
  // upstream CSP via proxy_hide_header.
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  const response = NextResponse.next({ request: { headers: requestHeaders } });

  // CSP intentionally NOT set here — the front-line nginx reverse proxy
  // owns it. nginx generates a per-request nonce, injects it onto every
  // <script> tag in the HTML response via sub_filter, and emits a
  // matching Content-Security-Policy header. nginx also calls
  // proxy_hide_header Content-Security-Policy on this upstream so any
  // accidental CSP we set here would be stripped — keeping the
  // single-source-of-truth invariant.
  //
  // Other security headers stay here because they're nonce-independent.
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  response.headers.set("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()");

  // Pass nonce via header for script injection (style nonce not used — see style-src above)
  response.headers.set("X-Nonce", nonce);

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
