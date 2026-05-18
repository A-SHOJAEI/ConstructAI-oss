import { expect, test, describe, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(),
}));

import { usePathname } from "next/navigation";
import { Breadcrumbs } from "@/components/breadcrumbs";

describe("Breadcrumbs", () => {
  test("does not render for single-segment paths", () => {
    vi.mocked(usePathname).mockReturnValue("/projects");
    const { container } = render(<Breadcrumbs />);
    expect(container.innerHTML).toBe("");
  });

  test("renders breadcrumbs for multi-segment paths", () => {
    vi.mocked(usePathname).mockReturnValue("/safety/cameras");
    render(<Breadcrumbs />);
    expect(screen.getByText("Home")).toBeInTheDocument();
    expect(screen.getByText("Safety")).toBeInTheDocument();
    expect(screen.getByText("Cameras")).toBeInTheDocument();
  });

  test("last segment is not a link", () => {
    vi.mocked(usePathname).mockReturnValue("/safety/alerts");
    render(<Breadcrumbs />);
    const alerts = screen.getByText("Alerts");
    expect(alerts.tagName).toBe("SPAN");
  });

  test("Home is always a link", () => {
    vi.mocked(usePathname).mockReturnValue("/safety/cameras");
    render(<Breadcrumbs />);
    const home = screen.getByText("Home");
    expect(home.closest("a")).toHaveAttribute("href", "/");
  });
});
