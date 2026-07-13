import { api } from "./api";
import type { CoverageSynthesis, SynthesisTimelineItem } from "./types";

/** SWR key for an article's related-coverage list; null (no fetch) until the
 * route param resolves. */
export const relatedKey = (id: number | string | undefined | null) =>
  id ? `/articles/${id}/related` : null;

/** The lazy "synthesize coverage" call — one LLM request over stored
 * summaries, only ever fired by an explicit tap. */
export function synthesizeCoverage(articleId: number): Promise<CoverageSynthesis> {
  return api<CoverageSynthesis>(`/articles/${articleId}/related-synthesis`, {
    method: "POST",
  });
}

/** Structured timeline rows, or null when the backend fell back to raw
 * markdown (rendered through <Markdown> instead). */
export function timelineRows(synthesis: CoverageSynthesis): SynthesisTimelineItem[] | null {
  return synthesis.timeline && synthesis.timeline.length > 0 ? synthesis.timeline : null;
}
