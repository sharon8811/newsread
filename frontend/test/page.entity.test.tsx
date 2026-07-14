import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EntityPage from "@/app/(app)/entity/[id]/page";
import { makeArticle } from "./fixtures";
import type { EntityPage as EntityPageData } from "@/lib/api";

const { swrMock, routerMock, paramsMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  routerMock: { push: vi.fn() },
  paramsMock: { value: { id: "7" } as { id: string } | null },
}));
vi.mock("swr", () => ({ default: swrMock }));
vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => paramsMock.value,
}));

function makeEntityPage(over: Partial<EntityPageData> = {}): EntityPageData {
  return {
    id: 7,
    kind: "person",
    key: "peter thiel",
    url: "",
    name: "Peter Thiel",
    badge: {},
    articles: [],
    ...over,
  };
}

describe("<EntityPage>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    routerMock.push.mockClear();
    paramsMock.value = { id: "7" };
  });

  it("renders nothing while loading and passes the id-keyed SWR key", () => {
    swrMock.mockReturnValue({ data: undefined });
    const { container } = render(<EntityPage />);
    expect(container.firstChild).toBeNull();
    expect(swrMock).toHaveBeenCalledWith("/entities/7", expect.anything());
  });

  it("skips fetching without an id param", () => {
    paramsMock.value = null;
    swrMock.mockReturnValue({ data: undefined });
    render(<EntityPage />);
    expect(swrMock).toHaveBeenCalledWith(null, expect.anything());
  });

  it("shows an error state", () => {
    swrMock.mockReturnValue({ data: undefined, error: new Error("404") });
    render(<EntityPage />);
    expect(screen.getByText("This entity could not be loaded.")).toBeInTheDocument();
  });

  it("renders the header, empty state, and no external link without a url", () => {
    swrMock.mockReturnValue({ data: makeEntityPage() });
    render(<EntityPage />);
    expect(screen.getByText("Person")).toBeInTheDocument();
    expect(screen.getByText("Peter Thiel")).toBeInTheDocument();
    expect(screen.getByText("No articles from your feeds mention this yet.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Open source page")).toBeNull();
  });

  it("falls back to the raw kind for unknown kinds and links the source url", () => {
    swrMock.mockReturnValue({
      data: makeEntityPage({ kind: "hf_model", url: "https://hf.co/x", name: "x" }),
    });
    render(<EntityPage />);
    expect(screen.getByText("Hugging Face model")).toBeInTheDocument();
    expect(screen.getByLabelText("Open source page")).toHaveAttribute("href", "https://hf.co/x");
  });

  it("lists articles with unread dots and navigates on click", async () => {
    swrMock.mockReturnValue({
      data: makeEntityPage({
        articles: [
          makeArticle({ id: 5, title: "Unread piece", is_read: false }),
          makeArticle({ id: 6, title: "Read piece", is_read: true, published_at: null }),
        ],
      }),
    });
    const { container } = render(<EntityPage />);
    expect(screen.getByText("Unread piece")).toBeInTheDocument();
    expect(container.querySelectorAll(".dot-unread")).toHaveLength(1);
    await userEvent.click(screen.getByText("Read piece"));
    expect(routerMock.push).toHaveBeenCalledWith("/article/6");
  });
});
