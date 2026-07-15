import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import NotFound from "@/app/not-found";

describe("root not-found page", () => {
  it("renders the 404 message with a link home", () => {
    render(<NotFound />);
    expect(screen.getByText(/404 — not found/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to your inbox/i }),
    ).toHaveAttribute("href", "/");
  });
});
