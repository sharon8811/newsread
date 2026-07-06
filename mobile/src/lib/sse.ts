/** Incremental server-sent-events decoder: feed it text chunks as they
 * arrive, get back the JSON payloads of every complete `data:` frame.
 * Pure (no I/O) so the chunk-boundary handling is unit-testable. */
export function createSSEDecoder(): (chunk: string) => unknown[] {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    const events: unknown[] = [];
    let frameEnd;
    while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) events.push(JSON.parse(line.slice(6)));
      }
    }
    return events;
  };
}
