import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Loading from "@/app/(app)/loading";

describe("(app) loading fallback", () => {
  it("renders an accessible pending indicator", () => {
    const { container } = render(<Loading />);
    expect(screen.getByRole("status", { name: /loading/i })).toBeInTheDocument();
    expect(container.querySelectorAll(".typing-dot")).toHaveLength(3);
  });
});
