import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import PrivateHistoryImage from "@/components/PrivateHistoryImage";

const { swrMock, objectUrlMock, revokeObjectUrlMock, state } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  objectUrlMock: vi.fn(() => "blob:private-history-image"),
  revokeObjectUrlMock: vi.fn(),
  state: { data: undefined as Blob | undefined },
}));

vi.mock("swr", () => ({ default: swrMock }));
vi.mock("@/lib/api", () => ({ apiBlob: vi.fn() }));

describe("PrivateHistoryImage", () => {
  beforeEach(() => {
    state.data = undefined;
    swrMock.mockImplementation(() => ({ data: state.data }));
    Object.defineProperty(URL, "createObjectURL", {
      value: objectUrlMock,
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectUrlMock,
      configurable: true,
    });
  });

  it("disables the authenticated request and renders nothing without an image id", () => {
    const { container } = render(
      <PrivateHistoryImage imageId={null} alt="Missing image" />,
    );

    expect(swrMock).toHaveBeenCalledWith(
      null,
      expect.any(Function),
      expect.objectContaining({
        revalidateOnFocus: false,
        shouldRetryOnError: false,
      }),
    );
    expect(container).toBeEmptyDOMElement();
    expect(objectUrlMock).not.toHaveBeenCalled();
  });

  it("renders a private blob and revokes its URL when unmounted", () => {
    const blob = new Blob(["image"], { type: "image/png" });
    state.data = blob;
    const { unmount } = render(
      <PrivateHistoryImage
        imageId={7}
        alt="Lead image"
        className="object-cover"
      />,
    );

    expect(swrMock).toHaveBeenCalledWith(
      "/history/images/7",
      expect.any(Function),
      expect.objectContaining({
        revalidateOnFocus: false,
        shouldRetryOnError: false,
      }),
    );
    expect(objectUrlMock).toHaveBeenCalledWith(blob);
    expect(screen.getByRole("img", { name: "Lead image" })).toHaveAttribute(
      "src",
      "blob:private-history-image",
    );
    expect(screen.getByRole("img", { name: "Lead image" })).toHaveClass(
      "object-cover",
    );

    unmount();
    expect(revokeObjectUrlMock).toHaveBeenCalledWith(
      "blob:private-history-image",
    );
  });

  it("revokes the previous object URL when the fetched blob changes", () => {
    objectUrlMock
      .mockReturnValueOnce("blob:private-history-image-first")
      .mockReturnValueOnce("blob:private-history-image-second");
    state.data = new Blob(["first"], { type: "image/png" });
    const { rerender } = render(
      <PrivateHistoryImage imageId={7} alt="History image" />,
    );

    state.data = new Blob(["second"], { type: "image/png" });
    rerender(<PrivateHistoryImage imageId={8} alt="History image" />);

    expect(objectUrlMock).toHaveBeenCalledTimes(2);
    expect(revokeObjectUrlMock).toHaveBeenCalledWith(
      "blob:private-history-image-first",
    );
  });
});
