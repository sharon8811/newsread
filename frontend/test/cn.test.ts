import { describe, expect, it } from "vitest";
import { cn } from "@/lib/cn";

describe("cn", () => {
  it("joins classes and drops falsy conditionals", () => {
    expect(cn("btn", false && "hidden", undefined, "mt-2")).toBe("btn mt-2");
  });

  it("resolves Tailwind conflicts in favor of the later class", () => {
    expect(cn("px-2 text-[11px]", "px-4")).toBe("text-[11px] px-4");
  });

  it("keeps non-conflicting custom component classes intact", () => {
    expect(cn("btn btn-accent", "w-full")).toBe("btn btn-accent w-full");
  });
});
