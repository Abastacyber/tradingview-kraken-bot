"""Tests du portefeuille simulé du gardien (étape 3)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_sim as G  # noqa: E402


def sig(day, long, buy_ok=None):
    buy_ok = long if buy_ok is None else buy_ok
    return {"jour_cloture_utc": day, "position_theorique": "ACHETEUR (BTC)" if long else "EN EUROS",
            "ema20_au_dessus_ema50": buy_ok, "prix_au_dessus_ema200": buy_ok}


def test_full_cycle_net_of_fees():
    pf = G.new_portfolio(100)
    buy = G.apply_signal(pf, sig("2026-12-01", True), 50_000, 0.004)
    assert buy["type"].startswith("ACHAT") and pf["en_position"]
    assert pf["btc"] == pytest.approx(99.6 / 50_000)
    assert G.apply_signal(pf, sig("2026-12-02", True), 55_000, 0.004) is None  # on garde
    sell = G.apply_signal(pf, sig("2026-12-03", False), 55_000, 0.004)
    assert sell["type"].startswith("VENTE") and not pf["en_position"]
    expected = 99.6 / 50_000 * 55_000 * 0.996
    assert pf["euros"] == pytest.approx(expected)
    assert sell["resultat_net_trade_pct"] == pytest.approx((expected / 100 - 1) * 100, abs=0.01)


def test_one_decision_per_closed_day():
    pf = G.new_portfolio(100)
    assert G.apply_signal(pf, sig("2026-12-01", True), 50_000, 0.004) is not None
    assert G.apply_signal(pf, sig("2026-12-01", False), 40_000, 0.004) is None  # même jour : ignoré
    assert pf["en_position"]


def test_no_catch_up_buy_when_conditions_not_met():
    # le signal « tient » une position ancienne, mais les conditions d'achat ne sont plus réunies aujourd'hui
    pf = G.new_portfolio(100)
    assert G.apply_signal(pf, sig("2026-12-01", True, buy_ok=False), 50_000, 0.004) is None
    assert not pf["en_position"]


def test_summary_and_persistence(tmp_path):
    pf = G.new_portfolio(100)
    G.apply_signal(pf, sig("2026-12-01", True), 50_000, 0.004)
    path = str(tmp_path / "pf.json")
    G.save(path, pf)
    back = G.load(path)
    s = G.summary(back, 60_000)
    assert s["valeur_actuelle_eur"] == pytest.approx(99.6 / 50_000 * 60_000, abs=0.01)
    assert s["resultat_depuis_debut_pct"] > 0


def test_tick_runs_simulation_and_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("GARDIEN_STATE_FILE", str(tmp_path / "pf.json"))
    sys.modules.pop("app", None)
    import app as A
    from test_gardien import series
    A._gardien_checks.clear()
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    monkeypatch.setattr(A.gardien_sim, "fetch_price", lambda pair: 400.0)
    entry = A._gardien_tick()
    assert entry["operation_simulee"].startswith("ACHAT")
    assert A._gardien_tick().get("operation_simulee") is None  # même jour : pas de second ordre
    pf = A.app.test_client().get("/gardien/portefeuille").get_json()
    assert pf["en_position"] is True and pf["ordres_reels"].startswith("aucun")


def test_change_branch_also_simulates(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    from test_gardien import series
    A._gardien_checks.clear()
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([400 - i for i in range(300)]))
    assert A._gardien_tick()["position"] == "EN EUROS"
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(301)]))
    e = A._gardien_tick()
    assert e["evenement"].startswith("CHANGEMENT") and e["operation_simulee"].startswith("ACHAT")
