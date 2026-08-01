import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import QuotaSection from "@/components/QuotaSection";
import type { QuotaStatus } from "@/lib/api";

const { swrState } = vi.hoisted(() => ({
  swrState: { data: undefined as QuotaStatus | undefined },
}));
vi.mock("swr", () => ({ default: () => ({ data: swrState.data }) }));

function makeQuota(over: Partial<QuotaStatus> = {}): QuotaStatus {
  return {
    tier_key: "free",
    tier_name: "Free",
    allowance: 100,
    used: 37,
    period_start: "2026-08-01",
    resets_on: "2026-09-01",
    exempt: false,
    ...over,
  };
}

describe("QuotaSection", () => {
  beforeEach(() => {
    swrState.data = makeQuota();
  });

  it("renders nothing while loading", () => {
    swrState.data = undefined;
    const { container } = render(<QuotaSection />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows tier, usage, and reset date with no purchase controls", () => {
    render(<QuotaSection />);
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.getByText(/37 of 100 articles this month/)).toBeInTheDocument();
    expect(screen.getByText(/resets on 2026-09-01/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull(); // read-only by design
  });

  it("reads unlimited tiers without a meter", () => {
    swrState.data = makeQuota({ tier_name: "Unlimited", allowance: null, used: 12 });
    const { container } = render(<QuotaSection />);
    expect(screen.getByText(/12 articles this month · no limit/)).toBeInTheDocument();
    expect(container.querySelector(".h-\\[4px\\]")).toBeNull();
  });

  it("marks administrators as never limited, with no meter to run red", () => {
    // Even on a finite tier: exempt accounts are never enforced, so no
    // exhausted-plan presentation may appear.
    swrState.data = makeQuota({ exempt: true, allowance: 100, used: 100 });
    const { container } = render(<QuotaSection />);
    expect(screen.getByText(/administrator, never limited/)).toBeInTheDocument();
    expect(screen.queryByText(/of 100 articles/)).toBeNull();
    expect(container.querySelector(".h-\\[4px\\]")).toBeNull();
  });

  it("turns the meter red at the limit", () => {
    swrState.data = makeQuota({ used: 100 });
    const { container } = render(<QuotaSection />);
    const fill = container.querySelector(".h-\\[4px\\] > div") as HTMLElement;
    expect(fill.style.width).toBe("100%");
    expect(fill.style.background).toContain("--danger");
  });
});
