import { expect, test, describe, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/reset-password",
  useSearchParams: () => ({
    get: (key: string) => (key === "token" ? "valid-reset-token" : null),
  }),
}));

import ResetPasswordPage from "@/app/reset-password/page";

describe("Reset Password Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  test("renders set new password heading", () => {
    render(<ResetPasswordPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Set New Password");
  });

  test("renders password and confirm password fields", () => {
    render(<ResetPasswordPage />);
    expect(screen.getByLabelText(/new password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm password/i)).toBeInTheDocument();
  });

  test("shows error when passwords do not match", async () => {
    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByLabelText(/new password/i), {
      target: { value: "password123" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "password456" },
    });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => {
      expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument();
    });
  });

  test("shows success on valid reset", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ detail: "Password has been reset successfully." }),
    });

    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByLabelText(/new password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => {
      expect(screen.getByText(/password reset successful/i)).toBeInTheDocument();
    });
  });

  test("shows error on API failure", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Invalid or expired reset token." }),
    });

    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByLabelText(/new password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => {
      expect(screen.getByText(/invalid or expired reset token/i)).toBeInTheDocument();
    });
  });

  test("renders sign in link on success", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ detail: "ok" }),
    });

    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByLabelText(/new password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.change(screen.getByLabelText(/confirm password/i), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: /reset password/i }));

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
    });
  });
});
