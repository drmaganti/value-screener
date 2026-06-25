"""
Unit tests for the deterministic indicator math.

These functions are the backbone of the technical screen. They take known
inputs and must produce known outputs; if any of these break, every downstream
signal is silently wrong. No network, no LLM, no randomness here.
"""
import value_screener as vs


# ── RSI ─────────────────────────────────────────────────────────────────────

def test_rsi_all_gains_is_100():
    # A strictly rising series has no losses -> RSI pegs at 100.
    assert vs._rsi([float(i) for i in range(1, 30)]) == 100.0


def test_rsi_all_losses_is_0():
    # A strictly falling series has no gains -> RSI bottoms at 0.
    assert vs._rsi([float(i) for i in range(30, 1, -1)]) == 0.0


def test_rsi_short_series_returns_neutral():
    # Not enough data to compute -> neutral 50, never a crash.
    assert vs._rsi([1, 2, 3]) == 50.0


def test_rsi_balanced_series_is_midrange():
    # Equal up/down moves -> average gain == average loss -> RSI near 50.
    closes = [100, 101] * 8
    assert 45 <= vs._rsi(closes) <= 55


# ── Moving average ──────────────────────────────────────────────────────────

def test_ma_simple_average():
    assert vs._ma([10, 20, 30], 3) == 20.0


def test_ma_uses_only_the_last_window():
    # Window of 2 should ignore the older values entirely.
    assert vs._ma([1, 1, 1, 4, 4], 2) == 4.0


# ── Pullback / correction detection ─────────────────────────────────────────

def test_pullback_is_distance_from_52w_high():
    closes = [50.0] * 200 + [100.0, 80.0]   # high 100, last 80 -> 20% off
    t = vs.compute_technicals(vs.PriceHistory("X", closes))
    assert abs(t.pullback_pct - 0.20) < 1e-6


def test_is_corrected_triggers_at_pullback_threshold():
    closes = [50.0] * 250 + [100.0, 90.0]   # exactly 10% off the high
    t = vs.compute_technicals(vs.PriceHistory("X", closes))
    assert vs.is_corrected(t)


def test_is_corrected_triggers_on_oversold_rsi():
    # Gentle uptrend then a sharp drop pushes RSI below the oversold line
    # without a huge pullback off the high.
    closes = [100.0 + i * 0.1 for i in range(250)] + [v for v in (118, 110, 104, 100, 98)]
    t = vs.compute_technicals(vs.PriceHistory("X", closes))
    assert t.rsi <= vs.THRESHOLDS["rsi_oversold"]
    assert vs.is_corrected(t)


def test_flat_high_stock_is_not_corrected():
    # No pullback and not oversold -> should not be flagged.
    t = vs.compute_technicals(vs.PriceHistory("X", [100.0] * 260))
    assert not vs.is_corrected(t)


def test_falling_knife_penalised_vs_dip_in_uptrend():
    # Same-size pullback, but one stock is still above its 200-day average
    # (dip in an uptrend) and the other is below it (broken trend). The
    # in-uptrend name must score higher.
    uptrend = [50.0 + i * 0.5 for i in range(250)] + [175.0, 150.0]   # above 200ma
    broken = [200.0 - i * 0.5 for i in range(250)] + [80.0, 70.0]     # below 200ma
    t_up = vs.compute_technicals(vs.PriceHistory("UP", uptrend))
    t_dn = vs.compute_technicals(vs.PriceHistory("DN", broken))
    assert t_up.above_200ma and not t_dn.above_200ma
    assert t_up.score > t_dn.score
