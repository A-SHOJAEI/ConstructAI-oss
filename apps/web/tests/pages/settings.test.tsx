import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/settings",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

// Mock useAuth hook
const mockUser = {
  id: "user-1",
  email: "test@example.com",
  full_name: "Test User",
  role: "admin",
  org_id: "org-1",
};
let mockAuthLoading = false;
let mockAuthUser: typeof mockUser | null = mockUser;

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: mockAuthUser,
    isLoading: mockAuthLoading,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

// Mock ProcoreConnection component
vi.mock("@/components/settings/procore-connection", () => ({
  ProcoreConnection: () => <div data-testid="procore-connection">Procore Connection</div>,
}));

import SettingsPage from "@/app/(dashboard)/settings/page";
import { renderWithProviders } from "../test-utils";

describe("Settings Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockAuthLoading = false;
    mockAuthUser = mockUser;
    global.fetch = vi.fn();
  });

  test("renders Settings heading", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Settings");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Manage your account and application preferences")).toBeInTheDocument();
  });

  test("renders Profile section heading", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Profile")).toBeInTheDocument();
  });

  test("renders user profile info when authenticated", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Test User")).toBeInTheDocument();
    expect(screen.getByText("test@example.com")).toBeInTheDocument();
  });

  test("renders Full Name and Email fields", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Full Name")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
  });

  test("renders Edit button for name field", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
  });

  test("shows loading skeleton when auth is loading", () => {
    mockAuthLoading = true;
    renderWithProviders(<SettingsPage />);
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  test("shows not authenticated message when user is null", () => {
    mockAuthUser = null;
    renderWithProviders(<SettingsPage />);
    expect(
      screen.getByText("Not authenticated. Please log in to view your profile."),
    ).toBeInTheDocument();
  });

  test("renders Notifications section heading", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Notifications")).toBeInTheDocument();
  });

  test("renders notification preferences when prefs data loads", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        email_notifications: true,
        safety_alerts: true,
        schedule_changes: false,
        daily_digest: false,
      }),
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => {
      expect(screen.getByText("Email Notifications")).toBeInTheDocument();
      expect(screen.getByText("Safety Alerts")).toBeInTheDocument();
      expect(screen.getByText("Schedule Changes")).toBeInTheDocument();
      expect(screen.getByText("Daily Digest")).toBeInTheDocument();
    });
  });

  test("renders Integrations section with Procore component", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Integrations")).toBeInTheDocument();
    expect(screen.getByTestId("procore-connection")).toBeInTheDocument();
  });

  test("renders API Keys section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("API Keys")).toBeInTheDocument();
    expect(screen.getByText("API Key Management")).toBeInTheDocument();
  });
});
