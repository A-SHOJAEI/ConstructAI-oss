import { expect, test, describe, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Pagination } from "@/components/pagination";

describe("Pagination", () => {
  test("does not render when total fits in one page", () => {
    const { container } = render(
      <Pagination page={1} pageSize={10} total={5} onChange={vi.fn()} />,
    );
    expect(container.innerHTML).toBe("");
  });

  test("renders with correct showing text", () => {
    render(<Pagination page={1} pageSize={10} total={25} onChange={vi.fn()} />);
    expect(screen.getByText(/Showing 1-10 of 25/)).toBeInTheDocument();
  });

  test("disables previous button on first page", () => {
    render(<Pagination page={1} pageSize={10} total={25} onChange={vi.fn()} />);
    expect(screen.getByText("Previous")).toBeDisabled();
  });

  test("disables next button on last page", () => {
    render(<Pagination page={3} pageSize={10} total={25} onChange={vi.fn()} />);
    expect(screen.getByText("Next")).toBeDisabled();
  });

  test("calls onChange with correct page on click", () => {
    const onChange = vi.fn();
    render(<Pagination page={1} pageSize={10} total={25} onChange={onChange} />);
    fireEvent.click(screen.getByText("Next"));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  test("highlights current page", () => {
    render(<Pagination page={2} pageSize={10} total={25} onChange={vi.fn()} />);
    const pageBtn = screen.getByText("2");
    expect(pageBtn.className).toContain("bg-blue-600");
  });
});
