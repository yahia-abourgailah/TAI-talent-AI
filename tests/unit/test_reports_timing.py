"""The time report's arithmetic, without a database."""

from reports.timing import quantile


def test_quantiles_interpolate_like_percentile_cont():
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert quantile([float(n) for n in range(1, 11)], 0.9) == 9.1
    assert quantile([7.0], 0.9) == 7.0


def test_no_scores_gives_no_quantile():
    assert quantile([], 0.5) is None
