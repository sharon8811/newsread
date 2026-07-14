import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import EntityCard from "@/components/EntityCard";
import { makeEntityFull } from "./fixtures";
import type { EntityFull } from "@/lib/api";

function ent(over: Partial<EntityFull>): EntityFull {
  return makeEntityFull(over);
}

describe("<EntityCard>", () => {
  it("renders nothing when no entity has data", () => {
    const { container } = render(
      <EntityCard entities={[ent({ data: {} }), ent({ data: {} })]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(<EntityCard entities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a github primary with stars (+delta), forks, issues and footer", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            key: "a/b",
            url: "https://github.com/a/b",
            data: {
              full_name: "a/b",
              description: "a repo",
              stargazers_count: 1200,
              forks_count: 300,
              open_issues_count: 42,
              language: "Python",
              license: "MIT",
              pushed_at: "2024-01-01T00:00:00Z",
            },
            deltas: { stargazers_count_delta_7d: 50 },
          }),
        ]}
      />,
    );
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("a/b")).toBeInTheDocument();
    expect(screen.getByText("a repo")).toBeInTheDocument();
    expect(screen.getByText(/★ 1,200 \(\+50 this week\)/)).toBeInTheDocument();
    expect(screen.getByText(/⑂ 300/)).toBeInTheDocument();
    expect(screen.getByText(/42 issues/)).toBeInTheDocument();
    // footer: language, license, updated
    expect(screen.getByText(/Python · MIT · updated/)).toBeInTheDocument();
  });

  it("renders github with a negative delta (no plus sign)", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            data: { full_name: "x/y", stargazers_count: 5000 },
            deltas: { stargazers_count_delta_7d: -80 },
            snapshots: [],
          }),
        ]}
      />,
    );
    expect(screen.getByText(/★ 5,000 \(-80 this week\)/)).toBeInTheDocument();
  });

  it("renders github with a zero delta (no parenthetical)", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            data: { full_name: "x/y", stargazers_count: 5000 },
            deltas: { stargazers_count_delta_7d: 0 },
          }),
        ]}
      />,
    );
    expect(screen.getByText("★ 5,000")).toBeInTheDocument();
  });

  it("renders a sparkline when there are >= 3 numeric snapshots", () => {
    const { container } = render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            data: { full_name: "a/b", stargazers_count: 1200 },
            snapshots: [
              { captured_at: "2024-01-03T00:00:00Z", data: { stargazers_count: 1200 } },
              { captured_at: "2024-01-02T00:00:00Z", data: { stargazers_count: 1100 } },
              { captured_at: "2024-01-01T00:00:00Z", data: { stargazers_count: 1000 } },
            ],
          }),
        ]}
      />,
    );
    const polyline = container.querySelector("polyline");
    expect(polyline).not.toBeNull();
    expect(polyline?.getAttribute("points")).toBeTruthy();
  });

  it("renders a flat sparkline when all snapshot values are equal (range fallback)", () => {
    const { container } = render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            data: { full_name: "a/b", stargazers_count: 1000 },
            snapshots: [
              { captured_at: "3", data: { stargazers_count: 1000 } },
              { captured_at: "2", data: { stargazers_count: 1000 } },
              { captured_at: "1", data: { stargazers_count: 1000 } },
            ],
          }),
        ]}
      />,
    );
    expect(container.querySelector("polyline")).not.toBeNull();
  });

  it("omits the sparkline when there are fewer than 3 usable points", () => {
    const { container } = render(
      <EntityCard
        entities={[
          ent({
            kind: "github",
            data: { full_name: "a/b", stargazers_count: 1200 },
            snapshots: [
              { captured_at: "2", data: { stargazers_count: 1200 } },
              { captured_at: "1", data: { stargazers_count: null } },
            ],
          }),
        ]}
      />,
    );
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("omits the sparkline for kinds without a spark metric", () => {
    const { container } = render(
      <EntityCard
        entities={[
          ent({
            kind: "arxiv",
            data: { title: "Paper", primary_category: "cs.LG" },
            snapshots: [],
          }),
        ]}
      />,
    );
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("renders hf_model with downloads (+delta), likes, params and pipeline_tag", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "hf_model",
            data: {
              id: "org/model",
              downloads: 25000,
              likes: 1500,
              params: 7000000000,
              pipeline_tag: "text-generation",
              last_modified: "2024-01-01T00:00:00Z",
            },
            deltas: { downloads_delta_7d: 400 },
          }),
        ]}
      />,
    );
    expect(screen.getByText("Hugging Face model")).toBeInTheDocument();
    expect(screen.getByText("org/model")).toBeInTheDocument();
    expect(screen.getByText(/⬇ 25,000 \(\+400 this week\)/)).toBeInTheDocument();
    expect(screen.getByText(/♥ 1\.5k/)).toBeInTheDocument();
    expect(screen.getByText(/7\.0B params/)).toBeInTheDocument();
    expect(screen.getByText(/text-generation/)).toBeInTheDocument();
    expect(screen.getByText(/updated/)).toBeInTheDocument();
  });

  it("renders hf_dataset downloads without a delta", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "hf_dataset",
            data: { id: "org/ds", downloads: 900 },
            deltas: {},
          }),
        ]}
      />,
    );
    expect(screen.getByText("Hugging Face dataset")).toBeInTheDocument();
    expect(screen.getByText("⬇ 900")).toBeInTheDocument();
  });

  it("renders hf_model without downloads but with likes", () => {
    render(
      <EntityCard
        entities={[ent({ kind: "hf_model", data: { id: "org/model", likes: 12 }, deltas: {} })]}
      />,
    );
    expect(screen.getByText("♥ 12")).toBeInTheDocument();
  });

  it("renders youtube without a channel (empty stats)", () => {
    render(
      <EntityCard entities={[ent({ kind: "youtube", data: { title: "Just a Video" } })]} />,
    );
    expect(screen.getByText("Just a Video")).toBeInTheDocument();
  });

  it("renders arxiv with many authors (et al.) and a primary category", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "arxiv",
            data: {
              title: "Attention Is All You Need",
              abstract: "an abstract",
              authors: ["A", "B", "C", "D", "E"],
              primary_category: "cs.CL",
              published: "2024-01-01T00:00:00Z",
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText("arXiv")).toBeInTheDocument();
    expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
    expect(screen.getByText("an abstract")).toBeInTheDocument();
    expect(screen.getByText(/A, B, C et al\./)).toBeInTheDocument();
    expect(screen.getByText(/cs\.CL/)).toBeInTheDocument();
    expect(screen.getByText(/published/)).toBeInTheDocument();
  });

  it("renders arxiv with few authors (no et al.) and no category", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "arxiv",
            data: { title: "Short Paper", authors: ["Solo", "Duo"] },
          }),
        ]}
      />,
    );
    expect(screen.getByText("Solo, Duo")).toBeInTheDocument();
  });

  it("renders arxiv with no authors array at all (empty stats)", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "arxiv",
            data: { title: "No Authors" },
          }),
        ]}
      />,
    );
    expect(screen.getByText("No Authors")).toBeInTheDocument();
  });

  it("renders pypi with version, weekly downloads, python requirement and released_at footer", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "pypi",
            data: {
              name: "requests",
              version: "2.31.0",
              downloads_last_week: 15000000,
              requires_python: ">=3.8",
              released_at: "2024-01-01T00:00:00Z",
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText("PyPI")).toBeInTheDocument();
    expect(screen.getByText("requests")).toBeInTheDocument();
    expect(screen.getByText(/v2\.31\.0/)).toBeInTheDocument();
    expect(screen.getByText(/⬇ 15\.0M\/week/)).toBeInTheDocument();
    expect(screen.getByText(/Python >=3\.8/)).toBeInTheDocument();
    expect(screen.getByText(/released/)).toBeInTheDocument();
  });

  it("renders npm kind with a spark-less body", () => {
    render(
      <EntityCard
        entities={[
          ent({ kind: "npm", data: { name: "left-pad", version: "1.0.0" }, deltas: {} }),
        ]}
      />,
    );
    expect(screen.getByText("npm")).toBeInTheDocument();
    expect(screen.getByText("v1.0.0")).toBeInTheDocument();
  });

  it("renders youtube with a channel", () => {
    render(
      <EntityCard
        entities={[
          ent({ kind: "youtube", data: { title: "A Video", channel: "Cool Channel" } }),
        ]}
      />,
    );
    expect(screen.getByText("YouTube")).toBeInTheDocument();
    expect(screen.getByText("Cool Channel")).toBeInTheDocument();
  });

  it("falls back to the raw kind label and empty stats for an unknown kind", () => {
    render(
      <EntityCard
        entities={[
          ent({ kind: "mystery", data: { name: "thing" }, deltas: {}, snapshots: [] }),
        ]}
      />,
    );
    // KIND_LABELS has no mystery -> raw kind rendered
    expect(screen.getByText("mystery")).toBeInTheDocument();
    expect(screen.getByText("thing")).toBeInTheDocument();
  });

  it("uses the title fallback chain down to key when no name fields exist", () => {
    render(
      <EntityCard
        entities={[ent({ kind: "github", key: "fallback-key", data: { stargazers_count: 1 } })]}
      />,
    );
    expect(screen.getByText("fallback-key")).toBeInTheDocument();
  });

  it("prefers data.title, then data.id, then data.name for the title", () => {
    render(
      <EntityCard entities={[ent({ kind: "npm", key: "k", data: { name: "just-name" } })]} />,
    );
    expect(screen.getByText("just-name")).toBeInTheDocument();
  });

  it("uses the summary field for the description", () => {
    render(
      <EntityCard
        entities={[ent({ kind: "npm", data: { name: "pkg", summary: "the summary" } })]}
      />,
    );
    expect(screen.getByText("the summary")).toBeInTheDocument();
  });

  it("omits stats and footer paragraphs when there is nothing to show", () => {
    // github with no numeric metrics -> empty stats; no footer fields
    const { container } = render(
      <EntityCard entities={[ent({ kind: "github", data: { full_name: "a/b" } })]} />,
    );
    expect(screen.getByText("a/b")).toBeInTheDocument();
    // no description paragraph
    expect(container.querySelectorAll("p").length).toBe(1); // only the mono-label
  });

  it("renders secondary entities as chips, with and without a leading stat", () => {
    render(
      <EntityCard
        entities={[
          ent({
            id: 1,
            kind: "github",
            data: { full_name: "primary/repo", stargazers_count: 10 },
          }),
          ent({
            id: 2,
            kind: "pypi",
            key: "pkg",
            url: "https://pypi.org/project/pkg",
            data: { name: "pkg", version: "3.2.1" },
          }),
          ent({
            id: 3,
            kind: "mystery",
            key: "raw-chip",
            url: "https://example.com",
            data: { name: "raw-chip" },
          }),
        ]}
      />,
    );
    // chip with a stat (version)
    expect(screen.getByText("pkg")).toBeInTheDocument();
    expect(screen.getByText("v3.2.1")).toBeInTheDocument();
    // chip without any stat, unknown kind -> raw kind label + title
    expect(screen.getByText("raw-chip")).toBeInTheDocument();
    const chips = screen.getAllByRole("link").filter((a) => a.getAttribute("target") === "_blank");
    expect(chips.length).toBeGreaterThanOrEqual(3);
  });

  it("uses last_modified for the footer when pushed_at is absent", () => {
    render(
      <EntityCard
        entities={[
          ent({
            kind: "hf_model",
            data: { id: "m", downloads: 5, last_modified: "2024-01-01T00:00:00Z" },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/updated/)).toBeInTheDocument();
  });
});

describe("<EntityCard> name entities", () => {
  it("renders name entities as internal chips, never as the resource card", () => {
    render(
      <EntityCard
        entities={[
          ent({
            id: 7,
            kind: "person",
            key: "peter thiel",
            url: "",
            data: { name: "Peter Thiel" },
          }),
          ent({
            id: 3,
            kind: "github",
            key: "a/b",
            url: "https://github.com/a/b",
            data: { full_name: "a/b" },
          }),
        ]}
      />,
    );
    // The repo takes the card; the person is a chip linking in-app.
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    const chip = screen.getByText("Peter Thiel").closest("a");
    expect(chip).toHaveAttribute("href", "/entity/7");
    expect(chip).not.toHaveAttribute("target");
  });

  it("renders chips only when there is no enricher entity", () => {
    render(
      <EntityCard
        entities={[
          ent({ id: 8, kind: "org", key: "palantir", url: "", data: { name: "Palantir" } }),
          ent({ id: 9, kind: "product", key: "claude code", url: "", data: { name: "Claude Code" } }),
        ]}
      />,
    );
    expect(screen.getByText("Palantir").closest("a")).toHaveAttribute("href", "/entity/8");
    expect(screen.getByText("Claude Code").closest("a")).toHaveAttribute("href", "/entity/9");
    expect(screen.getByText("Org")).toBeInTheDocument();
    expect(screen.getByText("Product")).toBeInTheDocument();
    // No external-link resource card rendered.
    expect(document.querySelector('a[target="_blank"]')).toBeNull();
  });
});
