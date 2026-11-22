"""Étape 6 : MODE D'ESSAI — le vrai code du gardien rejoué sur une période passée (/gardien/essai)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_sim as G  # noqa: E402

CSV = os.path.join(ROOT, "data", "btceur_journalier.csv")


def history():
    return G.load_history_csv(CSV)


def run(start, end, outage=None, days=0, k=12.0):
    return G.trial(history(), start, end, 100.0, 0.004, k, 0.008, 0.005, outage, days)


def test_history_file_is_complete_and_ordered():
    c = history()
    assert len(c) > 2400 and all(len(x) == 5 for x in c)
    assert all(b[0] - a[0] == 86400 for a, b in zip(c, c[1:]))  # un jour après l'autre, sans trou
    assert all(x[3] <= min(x[1], x[4]) and x[2] >= max(x[1], x[4]) for x in c)  # plus bas ≤ o, c ≤ plus haut


def test_may_2021_crash_guardian_sells_on_signal():
    r = run("2021-04-01", "2021-08-01")
    ops = [(o["type"].split()[0], o["jour"]) for o in r["operations"]]
    assert ops == [("ACHAT", "2021-04-01"), ("VENTE", "2021-05-13"), ("ACHAT", "2021-07-30")]
    assert r["resultat"]["resultat_pct"] > r["comparaison_acheter_garder"]["resultat_pct"]
    assert r["resultat"]["perte_max_pct"] > r["comparaison_acheter_garder"]["perte_max_pct"]  # perte max plus faible
    assert r["periode"] == {"debut": "2021-04-01", "fin": "2021-08-01", "jours": 123}


def test_outage_delays_the_sale_and_costs_money():
    normal = run("2021-04-01", "2021-08-01")
    panne = run("2021-04-01", "2021-08-01", "2021-05-10", 7)
    sale = next(o for o in panne["operations"] if o["type"].startswith("VENTE"))
    assert sale["jour"] == "2021-05-16"  # vente au redémarrage (17/05), sur le dernier signal
    assert panne["resultat"]["valeur_finale_eur"] < normal["resultat"]["valeur_finale_eur"]
    down = [j for j in panne["jours"] if j["serveur"] == "EN PANNE"]
    assert len(down) == 7 and panne["panne_simulee"] == {"debut": "2021-05-10", "jours": 7}
    assert down[0]["jour"] == "2021-05-10" and down[-1]["jour"] == "2021-05-16"  # les jours affichés = jours de panne


def test_outage_with_tight_stop_is_caught_up_on_restart():
    """Pendant la panne, le stop « posé chez Kraken » est touché : au redémarrage, la vente est enregistrée
    au bon jour et au prix du stop (pas au prix du redémarrage)."""
    r = run("2021-04-01", "2021-08-01", "2021-05-10", 7, k=3.0)
    stop_sales = [o for o in r["operations"] if "STOP" in o["type"]]
    assert stop_sales and any("2021-05-10" <= o["jour"] < "2021-05-17" for o in stop_sales)


def test_trial_does_not_touch_live_portfolio_and_endpoint(monkeypatch, tmp_path):
    from test_gardien import series
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: (_ for _ in ()).throw(RuntimeError("hors ligne")))
    cl = A.app.test_client()
    r = cl.get("/gardien/essai?debut=2021-04-01&fin=2021-08-01&panne_debut=2021-05-10&panne_jours=7")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ordres"].startswith("aucun") and j["panne_simulee"]["jours"] == 7 and len(j["operations"]) == 3
    assert not os.path.exists(A.GARDIEN_STATE_FILE)  # le portefeuille simulé en direct n'a pas été créé/modifié
    assert cl.get("/gardien/essai?debut=2021-13-01").status_code == 400
    assert cl.get("/gardien/essai?stop_atr=0").status_code == 400
    assert cl.get("/gardien/essai?debut=2019-01-01&fin=2019-06-01").status_code == 400  # avant l'historique


def test_merge_history_prefers_kraken_for_recent_days():
    h = [(0, 1, 1, 1, 1.0), (86400, 1, 1, 1, 2.0), (172800, 1, 1, 1, 3.0)]
    live = [(86400, 9, 9, 9, 9.0), (172800, 9, 9, 9, 9.5)]
    assert G.merge_history(h, live) == [h[0]] + live
    assert G.merge_history(h, []) == h


def test_essai_page_is_served(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    r = A.app.test_client().get("/gardien/essai/page")
    assert r.status_code == 200 and r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    assert "Mode d'essai" in html and "/gardien/essai?" in html
