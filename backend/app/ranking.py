"""Shared ranking math: RRF fusion for hybrid search and centroid/cosine
similarity for embedding-based recommendations. Pure functions — the callers
own their queries."""

import math

# Candidates fetched per search leg before fusion.
SEARCH_POOL = 60
# Standard reciprocal-rank-fusion constant: dampens the head so one leg's
# top hit can't drown out consistent mid-rank agreement.
RRF_K = 60


def rrf_fuse(*legs: list[int]) -> list[int]:
    """Fuse ranked id lists with reciprocal rank fusion; best first, newest
    (highest id) winning ties."""
    scores: dict[int, float] = {}
    for leg in legs:
        for rank, item_id in enumerate(leg):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda item_id: (-scores[item_id], -item_id))


def centroid(vectors: list[list[float]]) -> list[float]:
    return [sum(dim) / len(vectors) for dim in zip(*vectors, strict=False)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
