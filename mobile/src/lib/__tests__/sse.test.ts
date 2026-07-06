import { createSSEDecoder } from "../sse";

describe("createSSEDecoder", () => {
  it("decodes complete frames", () => {
    const decode = createSSEDecoder();
    expect(decode('data: {"type":"delta","text":"hi"}\n\n')).toEqual([
      { type: "delta", text: "hi" },
    ]);
  });

  it("buffers frames split across chunks", () => {
    const decode = createSSEDecoder();
    expect(decode('data: {"type":"del')).toEqual([]);
    expect(decode('ta","text":"hi"}\n')).toEqual([]);
    expect(decode("\n")).toEqual([{ type: "delta", text: "hi" }]);
  });

  it("decodes multiple frames in one chunk", () => {
    const decode = createSSEDecoder();
    expect(decode('data: {"a":1}\n\ndata: {"b":2}\n\n')).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it("handles multi-line frames, ignoring non-data lines", () => {
    const decode = createSSEDecoder();
    expect(decode('event: message\ndata: {"a":1}\n\n')).toEqual([{ a: 1 }]);
  });

  it("keeps state across events", () => {
    const decode = createSSEDecoder();
    decode('data: {"a":1}\n\ndata: {"b"');
    expect(decode(":2}\n\n")).toEqual([{ b: 2 }]);
  });
});
