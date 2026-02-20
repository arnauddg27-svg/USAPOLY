"""Aggregator module: combines devigged probabilities across multiple sportsbooks.

Uses median with outlier removal (beyond outlier_sigma standard deviations)
to produce a robust "true probability" estimate resistant to manipulation
or stale lines from any single book.
"""

import statistics
from polyedge.models import BookLine, AggregatedProb


def aggregate_probs(
    lines: list[BookLine],
    min_books: int = 6,
    outlier_sigma: float = 2.5,
) -> AggregatedProb | None:
    """Aggregate devigged probabilities across books using median with outlier removal.

    Args:
        lines: Per-book devigged probability lines.
        min_books: Minimum number of books required (before and after outlier removal).
        outlier_sigma: Number of standard deviations beyond which a line is
            considered an outlier and dropped.

    Returns:
        AggregatedProb with the consensus probability, or None if fewer than
        min_books remain after outlier removal.
    """
    if len(lines) < min_books:
        return None

    probs_a = [line.prob_a for line in lines]
    median_a = statistics.median(probs_a)

    if len(probs_a) >= 2:
        stdev_a = statistics.stdev(probs_a)
    else:
        stdev_a = 0.0

    kept: list[BookLine] = []
    dropped = 0
    for line in lines:
        if stdev_a > 0 and abs(line.prob_a - median_a) > outlier_sigma * stdev_a:
            dropped += 1
        else:
            kept.append(line)

    if len(kept) < min_books:
        return None

    final_a = statistics.median([line.prob_a for line in kept])
    final_b = 1.0 - final_a

    return AggregatedProb(
        prob_a=final_a,
        prob_b=final_b,
        books_used=len(kept),
        outliers_dropped=dropped,
        method=kept[0].method if kept else "unknown",
        per_book=kept,
    )
