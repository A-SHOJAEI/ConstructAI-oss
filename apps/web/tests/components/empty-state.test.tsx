import { expect, test, describe, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EmptyState } from "@/components/empty-state";

describe("EmptyState", () => {
  test("renders title", () => {
    render(<EmptyState title="No items found" />);
    expect(screen.getByText("No items found")).toBeInTheDocument();
  });

  test("renders description when provided", () => {
    render(<EmptyState title="No items" description="Try adding some items." />);
    expect(screen.getByText("Try adding some items.")).toBeInTheDocument();
  });

  test("renders action button when provided", () => {
    const onClick = vi.fn();
    render(<EmptyState title="No items" action={{ label: "Add Item", onClick }} />);
    const btn = screen.getByText("Add Item");
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });

  test("does not render action button when not provided", () => {
    render(<EmptyState title="No items" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
