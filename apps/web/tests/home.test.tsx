import { expect, test, describe } from "vitest";
import { render, screen } from "@testing-library/react";
import Page from "@/app/page";

describe("Landing Page", () => {
  test("renders ConstructAI heading", () => {
    render(<Page />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("ConstructAI");
  });

  test("renders tagline", () => {
    render(<Page />);
    expect(screen.getByText(/AI-Powered Construction Management/i)).toBeInTheDocument();
  });

  test("renders Get Started link", () => {
    render(<Page />);
    expect(screen.getByRole("link", { name: /get started/i })).toHaveAttribute("href", "/login");
  });
});
