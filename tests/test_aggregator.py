import pytest
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.models import BookLine

def _make_lines(probs_a: list[float], bookmakers: list[str] = None) -> list[BookLine]:
    if bookmakers is None:
        bookmakers = [f"Book{i}" for i in range(len(probs_a))]
    return [BookLine(bookmaker=b, prob_a=p, prob_b=1-p, method="power")
            for b, p in zip(bookmakers, probs_a)]

class TestAggregation:
    def test_basic_median(self):
        lines = _make_lines([0.60, 0.62, 0.61, 0.63, 0.59, 0.61])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert abs(result.prob_a - 0.61) < 0.01
        assert result.books_used == 6
        assert result.outliers_dropped == 0

    def test_outlier_removal(self):
        lines = _make_lines([0.60, 0.61, 0.60, 0.62, 0.61, 0.60, 0.90])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert result.outliers_dropped >= 1
        assert result.prob_a < 0.65

    def test_insufficient_books(self):
        lines = _make_lines([0.60, 0.61, 0.62])
        result = aggregate_probs(lines, min_books=6)
        assert result is None

    def test_probs_sum_to_one(self):
        lines = _make_lines([0.55, 0.57, 0.56, 0.58, 0.55, 0.56])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert abs(result.prob_a + result.prob_b - 1.0) < 0.0001
