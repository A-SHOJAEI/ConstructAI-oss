import { expect, test, describe, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

// Mock next/navigation
const mockPush = vi.fn();
let mockSearchParamsGet: (key: string) => string | null = () => null;
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/login",
  useSearchParams: () => ({
    get: (key: string) => mockSearchParamsGet(key),
  }),
}));

// Must import after mocks
import LoginPage from "@/app/login/page";

describe("Login Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    mockSearchParamsGet = (_key: string) => null;
    // Default fetch mock — success
    global.fetch = vi.fn();
    // Clear any hash fragment
    window.history.replaceState(null, "", "/login");
  });

  test("renders sign in heading", () => {
    render(<LoginPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign In");
  });

  test("renders email and password fields", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  test("renders submit button", () => {
    render(<LoginPage />);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  test("renders forgot password link", () => {
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /forgot your password/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  test("shows error on failed login", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: "Invalid credentials" }),
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "bad@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "wrong" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
    });
  });

  test("calls fetch with credentials: include and redirects on successful login", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    });

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/auth/login"),
        expect.objectContaining({
          method: "POST",
          credentials: "include",
          body: JSON.stringify({ email: "user@example.com", password: "password123" }),
        }),
      );
    });

    // Cookie-based auth: tokens are set as httpOnly cookies by the backend.
    // No tokens should be stored in sessionStorage or localStorage.
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/projects");
    });

    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
  });

  test("disables button while loading", async () => {
    let resolveLogin: (value: unknown) => void;
    (global.fetch as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((resolve) => {
        resolveLogin = resolve;
      }),
    );

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      const submitBtn = screen.getByRole("button", { name: /signing in/i });
      expect(submitBtn).toBeDisabled();
      expect(submitBtn).toHaveTextContent("Signing in...");
    });

    // Clean up — resolve the pending fetch and let the component finish updating
    await act(async () => {
      resolveLogin!({
        ok: true,
        json: () => Promise.resolve({}),
      });
    });
  });

  describe("MFA flow", () => {
    test("shows MFA form when response contains mfa_required", async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            mfa_required: true,
            mfa_token: "mfa-token-abc",
          }),
      });

      render(<LoginPage />);

      fireEvent.change(screen.getByLabelText(/email/i), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText(/password/i), {
        target: { value: "password123" },
      });
      fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

      // Should show MFA code input
      await waitFor(() => {
        expect(screen.getByLabelText(/mfa code/i)).toBeInTheDocument();
      });

      // Should NOT have redirected yet
      expect(mockPush).not.toHaveBeenCalled();
    });

    test("submits MFA code with credentials: include and redirects on success", async () => {
      // Step 1: initial login returns mfa_required
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            mfa_required: true,
            mfa_token: "mfa-token-abc",
          }),
      });

      render(<LoginPage />);

      fireEvent.change(screen.getByLabelText(/email/i), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText(/password/i), {
        target: { value: "password123" },
      });
      fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/mfa code/i)).toBeInTheDocument();
      });

      // Step 2: submit MFA code
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({}),
      });

      fireEvent.change(screen.getByLabelText(/mfa code/i), {
        target: { value: "123456" },
      });
      fireEvent.click(screen.getByRole("button", { name: /verify/i }));

      await waitFor(() => {
        expect(global.fetch).toHaveBeenLastCalledWith(
          expect.stringContaining("/api/v1/auth/mfa/verify"),
          expect.objectContaining({
            method: "POST",
            credentials: "include",
            body: JSON.stringify({ mfa_token: "mfa-token-abc", code: "123456" }),
          }),
        );
      });

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/projects");
      });

      // No tokens stored client-side
      expect(sessionStorage.getItem("access_token")).toBeNull();
      expect(localStorage.getItem("access_token")).toBeNull();
    });
  });

  describe("SSO flow", () => {
    test("SSO success fragment triggers code exchange and redirects", async () => {
      // Simulate the SSO callback hash fragment
      window.history.replaceState(null, "", "/login#sso=success&code=sso-code-xyz");

      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });

      render(<LoginPage />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining("/api/v1/auth/sso/exchange"),
          expect.objectContaining({
            method: "POST",
            credentials: "include",
            body: JSON.stringify({ code: "sso-code-xyz" }),
          }),
        );
      });

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/projects");
      });

      // No tokens stored client-side
      expect(sessionStorage.getItem("access_token")).toBeNull();
      expect(localStorage.getItem("access_token")).toBeNull();
    });

    test("SSO failure shows error message", async () => {
      window.history.replaceState(null, "", "/login#sso=success&code=bad-code");

      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
        ok: false,
        json: () => Promise.resolve({ detail: "SSO code exchange failed" }),
      });

      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByText("SSO sign-in failed. Please try again.")).toBeInTheDocument();
      });

      expect(mockPush).not.toHaveBeenCalled();
    });

    test("SSO mfa_required fragment shows MFA form", async () => {
      window.history.replaceState(null, "", "/login#sso=mfa_required&mfa_token=sso-mfa-token-123");

      // The SSO MFA flow exchanges the opaque mfa_token code via POST to
      // /api/v1/auth/sso/exchange, which returns { mfa_required, mfa_token }.
      // Use route-aware mock to handle any additional fetch calls the page may make.
      (global.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
        if (typeof url === "string" && url.includes("/auth/sso/exchange")) {
          return {
            ok: true,
            json: () =>
              Promise.resolve({
                mfa_required: true,
                mfa_token: "real-mfa-jwt-token",
              }),
          };
        }
        if (typeof url === "string" && url.includes("/auth/me")) {
          return {
            ok: false,
            status: 401,
            json: () => Promise.resolve({ detail: "Not authenticated" }),
          };
        }
        return { ok: false, status: 404, json: () => Promise.resolve({}) };
      });

      render(<LoginPage />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining("/api/v1/auth/sso/exchange"),
          expect.objectContaining({
            method: "POST",
            credentials: "include",
            body: JSON.stringify({ code: "sso-mfa-token-123" }),
          }),
        );
      });

      await waitFor(() => {
        expect(screen.getByLabelText(/mfa code/i)).toBeInTheDocument();
      });

      expect(mockPush).not.toHaveBeenCalled();
    });
  });
});
