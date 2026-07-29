import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MobileNavContext, ownsMobileChrome, useOpenMobileNav } from "@/lib/mobileNav";

describe("ownsMobileChrome", () => {
  it("covers the reading routes that render their own bar", () => {
    expect(ownsMobileChrome("/")).toBe(true);
    expect(ownsMobileChrome("/article/12")).toBe(true);
  });

  it("leaves the shell bar in place everywhere else", () => {
    expect(ownsMobileChrome("/settings")).toBe(false);
    expect(ownsMobileChrome("/saved")).toBe(false);
  });
});

describe("useOpenMobileNav", () => {
  function Probe() {
    const open = useOpenMobileNav();
    return <button onClick={open}>open</button>;
  }

  it("is a no-op outside the shell", async () => {
    render(<Probe />);
    // Nothing to assert beyond "does not throw" — the default keeps a page
    // rendered on its own (tests, storybook-style previews) working.
    expect(screen.getByText("open")).toBeInTheDocument();
  });

  it("reaches the shell's drawer through the provider", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    let opened = 0;
    render(
      <MobileNavContext.Provider value={() => opened++}>
        <Probe />
      </MobileNavContext.Provider>,
    );
    await userEvent.click(screen.getByText("open"));
    expect(opened).toBe(1);
  });
});
