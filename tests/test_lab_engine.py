"""Vérifications du moteur de backtest ORION LAB sur des données synthétiques."""
import os, sys
import pytest
pd = pytest.importorskip("pandas")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lab"))
import engine as E  # noqa: E402


def bars(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def test_round_trip_cost_on_flat_market():
    df = bars([(100, 100, 100, 100)] * 4)
    ent = pd.Series([True, False, False, False], index=df.index)
    ex = pd.Series([False, True, False, False], index=df.index)
    r = E.run(df, ent, ex, E.Exits(), E.Costs(0.004, 0.004, 0.0005))
    t = r.trades[0]
    assert t.gross == 0
    assert t.net == pytest.approx(0.9995 * 0.996 / (1.0005 * 1.004) - 1)  # ≈ -0,90 %


def test_entry_at_next_open_no_lookahead():
    df = bars([(100, 101, 99, 100), (110, 111, 109, 110), (120, 121, 119, 120)])
    ent = pd.Series([True, False, False], index=df.index)
    r = E.run(df, ent, None, E.Exits(), E.Costs(0, 0, 0))
    assert r.trades[0].raw_entry == 110  # ouverture de la bougie suivante, pas 100


def test_gap_below_stop_exits_at_open():
    df = bars([(100, 100, 100, 100), (100, 101, 99, 100), (90, 91, 89, 90)])
    ent = pd.Series([True, False, False], index=df.index)
    r = E.run(df, ent, None, E.Exits(stop_pct=0.03), E.Costs(0, 0, 0))
    t = r.trades[0]
    assert t.reason == "stop_gap" and t.raw_exit == 90


def test_trailing_applies_from_next_bar():
    # +5 % puis retour : trailing 1 % activé à +0,5 %, sortie au plus haut -1 %
    df = bars([(100, 100, 100, 100), (100, 105, 100, 105), (105, 105, 100, 101)])
    ent = pd.Series([True, False, False], index=df.index)
    r = E.run(df, ent, None, E.Exits(stop_pct=0.05, trail_activate_pct=0.005, trail_gap_pct=0.01), E.Costs(0, 0, 0))
    t = r.trades[0]
    assert t.reason == "trail" and t.raw_exit == pytest.approx(105 * 0.99)
