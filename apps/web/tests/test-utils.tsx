import React from "react";
import { vi } from "vitest";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Standard mock for next/navigation. Call BEFORE importing the component.
 */
export function mockNavigation() {
  const push = vi.fn();
  const replace = vi.fn();
  const refresh = vi.fn();
  vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace, refresh }),
    usePathname: () => "/",
    useSearchParams: () => ({ get: vi.fn(() => null) }),
  }));
  return { push, replace, refresh };
}

/**
 * Standard mock for project store.
 */
export function mockProjectStore(projectId: string | null = "test-project-id") {
  vi.mock("@/stores/project-store", () => ({
    useProjectStore: (selector?: (state: Record<string, unknown>) => unknown) => {
      const state = {
        selectedProjectId: projectId,
        selectedProject: projectId
          ? { id: projectId, name: "Test Project", status: "active" }
          : null,
        setProject: vi.fn(),
        setSelectedProjectId: vi.fn(),
        clearProject: vi.fn(),
      };
      if (typeof selector === "function") return selector(state);
      return state;
    },
  }));
}

/**
 * Mock fetch to return specific data for API calls.
 */
export function mockFetch(data: unknown = {}, ok = true, status = 200) {
  (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
    ok,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
}

/**
 * Creates a fresh QueryClient suitable for testing (no retries, no caching).
 */
export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

/**
 * Render a component wrapped in QueryClientProvider for components that use react-query.
 */
export function renderWithProviders(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  const queryClient = createTestQueryClient();

  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  return { ...render(ui, { wrapper: Wrapper, ...options }), queryClient };
}
