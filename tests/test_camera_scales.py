"""Which scales earlier solves found for a camera are worth retrying a failed solve with."""
from photonfinder.solve_service import dedupe_scales as dedupe, otsu_threshold as otsu


# ---------------------------------------------------------------------------
# otsu_threshold
# ---------------------------------------------------------------------------

def test_otsu_clear_separation():
    # One count is clearly noise compared to the rest
    assert otsu([1, 100, 120]) > 1

def test_otsu_similar_counts_returns_zero():
    # 40 vs 50 — both legitimate, should not threshold
    assert otsu([40, 50]) == 0

def test_otsu_single_value_returns_zero():
    assert otsu([42]) == 0

def test_otsu_all_equal_returns_zero():
    assert otsu([10, 10, 10]) == 0

def test_otsu_threshold_below_noise():
    # Threshold should be between 2 and 80
    t = otsu([2, 80, 95])
    assert 2 <= t < 80


# ---------------------------------------------------------------------------
# dedupe_scales
# ---------------------------------------------------------------------------

def test_dedupe_empty():
    assert dedupe({}) == []

def test_dedupe_single():
    assert dedupe({1.5: 10}) == [1.5]

def test_dedupe_all_same_band():
    # 1.23, 1.24, 1.25 — all within 30% of each other → collapse to highest count
    result = dedupe({1.23: 2, 1.24: 45, 1.25: 3})
    assert result == [1.24]

def test_dedupe_two_distinct_setups():
    # 1.24 and 2.5 are more than 30% apart → both kept
    result = dedupe({1.24: 100, 2.50: 80})
    assert result == [1.24, 2.50]

def test_dedupe_30pct_boundary_exact():
    # scale * 1.30 == next scale → new band starts
    result = dedupe({1.00: 10, 1.30: 10})
    assert result == [1.00, 1.30]

def test_dedupe_just_inside_30pct():
    # 1.29 / 1.00 = 1.29 < 1.30 → same band
    result = dedupe({1.00: 5, 1.29: 5})
    assert len(result) == 1

def test_dedupe_winner_in_band_is_highest_count():
    result = dedupe({1.20: 3, 1.25: 50, 1.28: 4})
    assert result == [1.25]

def test_dedupe_otsu_removes_rare_outlier():
    # 0.5 has only 1 solve; 1.24 and 2.5 each have 80+ → Otsu should drop 0.5
    result = dedupe({0.50: 1, 1.24: 100, 2.50: 80})
    assert 0.50 not in result
    assert 1.24 in result
    assert 2.50 in result

def test_dedupe_otsu_keeps_minority_if_not_noise():
    # Two legitimate setups used at different rates (4:1 ratio) — not noise
    result = dedupe({1.24: 20, 2.50: 80})
    assert 1.24 in result
    assert 2.50 in result

def test_dedupe_otsu_never_removes_all():
    # Even if Otsu fires, at least one scale survives
    result = dedupe({1.24: 1})
    assert result == [1.24]

def test_dedupe_band_counts_accumulate_before_otsu():
    # 1.23 (2) + 1.24 (3) merge into one band with total 5;
    # 3.00 (1) is a separate band with total 1.
    # Otsu should see [5, 1] and drop the band total of 1 (3.00).
    result = dedupe({1.23: 2, 1.24: 3, 3.00: 1})
    assert 3.00 not in result
    assert any(s in result for s in (1.23, 1.24))

def test_dedupe_three_distinct_setups_all_kept():
    # Three clearly different focal lengths, all well-used
    result = dedupe({0.50: 30, 1.24: 100, 2.50: 60})
    assert result == [0.50, 1.24, 2.50]
