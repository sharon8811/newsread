import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import EntityBadges, { badgeParts } from "@/components/EntityBadges";
import { makeEntity } from "./fixtures";

describe("badgeParts", () => {
  it("github", () => {
    expect(badgeParts(makeEntity({ badge: { label: "a/b", stars: 1200, language: "Go", license: "MIT" } })))
      .toEqual(["★ 1.2k", "Go", "MIT"]);
  });

  it("github without optional fields", () => {
    expect(badgeParts(makeEntity({ badge: { label: "a/b" } }))).toEqual([]);
  });

  it("hf_model", () => {
    const parts = badgeParts(makeEntity({
      kind: "hf_model",
      badge: { label: "m", downloads: 5000, likes: 12, params: 7_000_000_000 },
    }));
    expect(parts).toEqual(["⬇ 5.0k", "♥ 12", "7.0B params"]);
  });

  it("hf_dataset uses same renderer", () => {
    const parts = badgeParts(makeEntity({ kind: "hf_dataset", badge: { label: "d", downloads: 100 } }));
    expect(parts[0]).toBe("⬇ 100");
  });

  it("arxiv", () => {
    const parts = badgeParts(makeEntity({
      kind: "arxiv",
      badge: { label: "p", primary_category: "cs.CL", authors_short: "Doe et al." },
    }));
    expect(parts).toEqual(["arXiv", "cs.CL", "Doe et al."]);
  });

  it("pypi", () => {
    expect(badgeParts(makeEntity({ kind: "pypi", badge: { label: "requests", version: "2.0" } })))
      .toEqual(["PyPI", "v2.0"]);
  });

  it("npm", () => {
    const parts = badgeParts(makeEntity({
      kind: "npm",
      badge: { label: "react", version: "18", downloads_last_week: 2000 },
    }));
    expect(parts).toEqual(["npm", "v18", "⬇ 2.0k/wk"]);
  });

  it("youtube", () => {
    expect(badgeParts(makeEntity({ kind: "youtube", badge: { label: "v", channel: "Ch" } })))
      .toEqual(["YouTube", "Ch"]);
  });

  it("unknown kind", () => {
    expect(badgeParts(makeEntity({ kind: "mystery", badge: { label: "x" } }))).toEqual([]);
  });
});

describe("<EntityBadges>", () => {
  it("renders the first badge's parts", () => {
    render(<EntityBadges entities={[makeEntity()]} />);
    expect(screen.getByText("★ 1.2k")).toBeInTheDocument();
    expect(screen.getByText(/Python/)).toBeInTheDocument();
  });

  it("returns null when nothing renderable", () => {
    const { container } = render(<EntityBadges entities={[makeEntity({ badge: { label: "a/b" } })]} />);
    expect(container.firstChild).toBeNull();
  });

  it("filters entities lacking a label", () => {
    const { container } = render(
      <EntityBadges entities={[makeEntity({ badge: { stars: 5 } })]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("respects the max prop", () => {
    render(
      <EntityBadges
        max={2}
        entities={[
          makeEntity({ id: 1 }),
          makeEntity({ id: 2, kind: "npm", badge: { label: "react", version: "18" } }),
          makeEntity({ id: 3, kind: "pypi", badge: { label: "req", version: "2" } }),
        ]}
      />,
    );
    expect(screen.getByText("★ 1.2k")).toBeInTheDocument();
    expect(screen.getByText("npm")).toBeInTheDocument();
    expect(screen.queryByText("PyPI")).not.toBeInTheDocument();
  });
});
