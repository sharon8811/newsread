import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErrorPage from "@/app/error";

describe("root error boundary page", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("logs the error and retries via unstable_retry", async () => {
    const error = Object.assign(new Error("boom"), { digest: "abc123" });
    const retry = vi.fn();
    render(<ErrorPage error={error} unstable_retry={retry} />);

    expect(console.error).toHaveBeenCalledWith(error);
    expect(screen.getByText(/reference: abc123/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("falls back to reset when unstable_retry is absent", async () => {
    const reset = vi.fn();
    render(<ErrorPage error={new Error("boom")} reset={reset} />);

    // No digest -> generic hint instead of a reference line.
    expect(screen.getByText(/may have been temporary/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("renders without a retry button when no callback is provided", () => {
    render(<ErrorPage error={new Error("boom")} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
