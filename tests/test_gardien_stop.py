"""Étape 5 : stop de protection SIMULÉ du gardien (suiveur 12 ATR, EXP06) + rejeu historique comparé au labo."""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien  # noqa: E402
import gardien_sim as G  # noqa: E402

DAY = 86400
T0 = 1_780_000_000 - 1_780_000_000 % DAY


def ohlc(rows, start=T0):
    """rows : (ouverture, plus haut, plus bas, clôture) → bougies (ts, o, h, l, c) journalières."""
    return [(start + i * DAY, *map(float, r)) for i, r in enumerate(rows)]


def day(i):
    return G._day(T0 + i * DAY)


def bought_on(i, price=100.0):
    """Portefeuille acheté au signal du jour i (exécuté au prix donné)."""
    pf = G.new_portfolio(100)
    sig = {"jour_cloture_utc": day(i), "position_theorique": "ACHETEUR (BTC)",
           "ema20_au_dessus_ema50": True, "prix_au_dessus_ema200": True}
    assert G.apply_signal(pf, sig, price, 0.004, now=T0 + (i + 1) * DAY)
    return pf


def test_atr_matches_lab():
    pd = pytest.importorskip("pandas")
    sys.path.insert(0, os.path.join(ROOT, "lab"))
    import engine as E
    rows = [(100 + i, 103 + i + (i % 7), 97 + i - (i % 5), 101 + i + (i % 3)) for i in range(60)]
    c = ohlc(rows)
    df = pd.DataFrame([r[1:] for r in c], columns=["open", "high", "low", "close"])
    assert gardien.atr(c) == pytest.approx(list(E.atr(df)), rel=1e-12)


def test_stop_rises_with_highs_and_never_falls():
    pf = bought_on(0)
    c = ohlc([(100, 100, 100, 100), (100, 120, 100, 118), (119, 119.5, 118.5, 119)])
    atrs = [1.0, 1.0, 5.0]
    G.process_closed_days(pf, c[:2], atrs[:2], k=2, fee_stop=0.008, slip_stop=0.005)
    assert pf["plus_haut_depuis_achat"] == 120 and pf["stop_protection"] == pytest.approx(118)
    G.process_closed_days(pf, c, atrs, k=2, fee_stop=0.008, slip_stop=0.005)
    assert pf["stop_protection"] == pytest.approx(118)  # 120 − 2×5 = 110 < 118 : le stop ne redescend pas
    assert pf["dernier_jour_stop"] == day(2)
    # rejouer les mêmes jours ne change rien
    G.process_closed_days(pf, c, atrs, k=2, fee_stop=0.008, slip_stop=0.005)
    assert pf["en_position"] and pf["stop_protection"] == pytest.approx(118)


def test_stop_hit_during_closed_day_sells_at_stop_with_taker_fee_and_slippage():
    pf = bought_on(0)
    btc = pf["btc"]
    c = ohlc([(100, 100, 100, 100), (100, 110, 100, 108), (107, 108, 90, 95)])
    ops = G.process_closed_days(pf, c, [1.0, 1.0, 1.0], k=5, fee_stop=0.008, slip_stop=0.005)
    assert len(ops) == 1 and ops[0]["type"].startswith("VENTE PAR STOP")
    assert ops[0]["stop"] == pytest.approx(105) and ops[0]["jour"] == day(2)
    assert not pf["en_position"] and pf["stop_protection"] is None
    assert pf["euros"] == pytest.approx(btc * 105 * 0.995 * 0.992)


def test_gap_below_stop_fills_at_open():
    pf = bought_on(0)
    btc = pf["btc"]
    c = ohlc([(100, 100, 100, 100), (100, 110, 100, 108), (80, 85, 78, 82)])
    ops = G.process_closed_days(pf, c, [1.0] * 3, k=5, fee_stop=0.008, slip_stop=0.005)
    assert ops[0]["prix"] == pytest.approx(80 * 0.995, abs=0.01) and "trou" in ops[0]["detail"]
    assert pf["euros"] == pytest.approx(btc * 80 * 0.995 * 0.992)


def test_catch_up_after_server_downtime():
    """Serveur arrêté 4 jours : au redémarrage, les jours manqués sont rejoués dans l'ordre ;
    le stop touché le 3e jour est appliqué avec le stop en place CE jour-là."""
    pf = bought_on(0)
    rows = [(100, 100, 100, 100), (100, 110, 100, 109), (109, 120, 108, 119), (119, 119, 111, 112), (112, 130, 112, 129)]
    c = ohlc(rows)
    G.process_closed_days(pf, c[:1], [1.0], k=5, fee_stop=0.008, slip_stop=0.005)
    ops = G.process_closed_days(pf, c, [1.0] * 5, k=5, fee_stop=0.008, slip_stop=0.005)  # rattrapage
    assert len(ops) == 1 and ops[0]["jour"] == day(3) and ops[0]["stop"] == pytest.approx(115)
    assert not pf["en_position"]  # la remontée du jour 4 n'y change rien : la vente a eu lieu


def test_intraday_hit_with_ticker_and_no_double_processing():
    pf = bought_on(0)
    c = ohlc([(100, 100, 100, 100), (100, 110, 100, 108)])
    G.process_closed_days(pf, c, [1.0, 1.0], k=5, fee_stop=0.008, slip_stop=0.005)
    now = T0 + 2 * DAY + 3600
    assert G.check_intraday(pf, {"prix": 107, "ouverture_jour": 108, "plus_bas_jour": 106}, 0.008, 0.005, now=now) is None
    op = G.check_intraday(pf, {"prix": 104, "ouverture_jour": 108, "plus_bas_jour": 103}, 0.008, 0.005, now=now)
    assert op["type"].startswith("VENTE PAR STOP") and op["stop"] == pytest.approx(105)
    # à la clôture de ce jour, la bougie ne déclenche pas une seconde vente
    c.append((T0 + 2 * DAY, 108.0, 108.0, 103.0, 104.0))
    assert G.process_closed_days(pf, c, [1.0] * 3, k=5, fee_stop=0.008, slip_stop=0.005) == []


def test_no_stop_before_first_closed_day_after_purchase():
    pf = bought_on(0)
    assert G.check_intraday(pf, {"prix": 1.0, "ouverture_jour": 1.0, "plus_bas_jour": 1.0}, 0.008, 0.005) is None
    assert pf["en_position"]


def test_fetch_ticker_parses_kraken(monkeypatch):
    monkeypatch.undo()  # on teste la vraie fonction (remplacée par défaut pour rester hors ligne)
    body = json.dumps({"error": [], "result": {"XXBTZEUR": {"c": ["56000.1", "0.1"], "o": "57000.0",
                                                            "l": ["55500.5", "55000.0"]}}}).encode()
    t = G.fetch_ticker(opener=lambda *a, **k: io.BytesIO(body))
    assert t == {"prix": 56000.1, "ouverture_jour": 57000.0, "plus_bas_jour": 55500.5}


def test_fetch_candles_keeps_ohlc():
    rows = [[T0 + i * DAY, "10", "12", "9", str(11 + i), "1", "1", 1] for i in range(3)]
    body = json.dumps({"error": [], "result": {"XXBTZEUR": rows, "last": 0}}).encode()
    out = gardien.fetch_daily_candles(now=T0 + 2 * DAY + 60, opener=lambda *a, **k: io.BytesIO(body))
    assert out == [(T0, 10.0, 12.0, 9.0, 11.0), (T0 + DAY, 10.0, 12.0, 9.0, 12.0)]


@pytest.mark.parametrize("sym", ["BTCEUR", "ETHEUR"])
@pytest.mark.parametrize("k", [12.0, 3.0])
def test_replay_matches_lab_trade_by_trade(sym, k):
    """REJEU HISTORIQUE : le vrai code du bot, rejoué jour par jour sur 2020-2026, prend EXACTEMENT les mêmes
    décisions que le laboratoire (achats, ventes sur signal, ventes par stop) et finit au même résultat."""
    pd = pytest.importorskip("pandas")
    if not os.path.isdir(os.path.join(ROOT, "lab", "data", sym, "1h")):
        pytest.skip("historique du laboratoire non téléchargé")
    sys.path.insert(0, os.path.join(ROOT, "lab"))
    import data as lab_data
    import engine as E
    import strategies as S
    d = lab_data.resample(lab_data.load("1h", sym=sym), "1D")
    candles = [(int(ts.timestamp()), r.open, r.high, r.low, r.close) for ts, r in d.iterrows()]
    pf = G.replay(candles, capital=100.0, fee=0.004, k=k, fee_stop=0.008, slip_stop=0.005)

    ent, xit = S.ema_state(d, 20, 50, 200)
    ent.iloc[:gardien.TREND] = False  # le bot attend que l'EMA200 soit chauffée
    r = E.run(d, ent, xit, E.Exits(trail_atr=k), E.Costs(0.004, 0.004, 0.0, fee_stop=0.008, slip_stop=0.005))

    nxt = lambda s: str((pd.Timestamp(s) + pd.Timedelta(days=1)).date())  # noqa: E731
    bot_entries = [nxt(t["jour"]) for t in pf["trades"] if t["type"].startswith("ACHAT")]
    bot_exits = [(t["jour"] if "STOP" in t["type"] else nxt(t["jour"]), "stop" if "STOP" in t["type"] else "signal")
                 for t in pf["trades"] if t["type"].startswith("VENTE")]
    lab_entries = [str(t.entry_time.date()) for t in r.trades]
    lab_exits = [(str(t.exit_time.date()), "stop" if t.reason in ("stop", "stop_gap", "trail") else "signal")
                 for t in r.trades if t.reason != "end"]
    assert bot_entries == lab_entries
    assert bot_exits == lab_exits
    if k == 3.0:
        assert any(kind == "stop" for _, kind in bot_exits)  # le chemin « stop » est bien exercé
    # même résultat final, à l'arrondi de convention de frais près (bot : frais pris sur les euros ;
    # labo : quantité = euros / (prix × (1 + frais)) → écart ≈ frais² par trade, 0,002 %)
    value = pf["euros"] + pf["btc"] * d.close.iloc[-1] * (1 - 0.004)
    assert value == pytest.approx(r.equity.iloc[-1] * 100, rel=3e-3)


def test_app_tick_triggers_simulated_stop(monkeypatch, tmp_path):
    from test_gardien import series
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    A._gardien_checks.clear()
    up = series([100 + i for i in range(300)])
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: up)
    monkeypatch.setattr(A.gardien_sim, "fetch_ticker",
                        lambda pair: {"prix": 400.0, "ouverture_jour": 400.0, "plus_bas_jour": 399.0})
    assert A._gardien_tick()["operation_simulee"].startswith("ACHAT")
    # jour suivant clôturé : le stop est posé (plus haut − 12 ATR) ; puis krach dans la journée en cours
    up2 = up + [(up[-1][0] + DAY, 400.0)]
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: up2)
    A._gardien_cache.update({"ts": 0.0})
    A._gardien_tick()
    pf = A.app.test_client().get("/gardien/portefeuille").get_json()
    assert pf["en_position"] and pf["stop_protection"] == pytest.approx(400 - 12 * gardien.atr(up2)[-1], abs=0.01)
    assert "12 × ATR14" in pf["regle_stop"]
    monkeypatch.setattr(A.gardien_sim, "fetch_ticker",
                        lambda pair: {"prix": 300.0, "ouverture_jour": 400.0, "plus_bas_jour": 290.0})
    e = A._gardien_tick()
    assert "VENTE PAR STOP" in e["operation_simulee"]
    pf = A.app.test_client().get("/gardien/portefeuille").get_json()
    assert not pf["en_position"] and pf["trades"][-1]["type"].startswith("VENTE PAR STOP")
