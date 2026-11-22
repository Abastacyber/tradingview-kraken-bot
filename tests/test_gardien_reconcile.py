"""Réconciliation ORION ↔ Kraken (lecture seule) : scénarios d'écart, avec de faux soldes et de faux ordres."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_reconcile as R  # noqa: E402


def bal(btc=0.0, eur=0.0, eur_free=None):
    return {"total": {"BTC": btc, "EUR": eur}, "free": {"BTC": btc, "EUR": eur if eur_free is None else eur_free}}


def pf_euros(euros=100.0):
    return {"en_position": False, "btc": 0.0, "euros": euros, "stop_protection": None}


def pf_btc(btc=0.002, stop=40000.0):
    return {"en_position": True, "btc": btc, "euros": 0.0, "stop_protection": stop}


def stop_order(amount=0.002, trigger=40000.0):
    return {"id": "S1", "side": "sell", "type": "stop-loss", "amount": amount, "triggerPrice": trigger}


def status(out, name):
    return next(c["statut"] for c in out["controles"] if c["controle"].startswith(name))


def test_in_euros_all_good():
    out = R.reconcile(pf_euros(100), bal(0, 150), [], simulation=False)
    assert out["verdict"] == "OK" and out["pret_pour_le_reel"]


def test_in_euros_missing_money_blocks():
    out = R.reconcile(pf_euros(100), bal(0, 2.1), [], simulation=True)
    assert out["verdict"] == "BLOQUANT" and status(out, "Euros") == "BLOQUANT"
    assert "97.90" in next(c["detail"] for c in out["controles"] if c["controle"].startswith("Euros"))
    assert "SIMULATION" in out["mode"]


def test_in_position_with_correct_stop():
    out = R.reconcile(pf_btc(), bal(0.002, 0), [stop_order()], simulation=False)
    assert out["verdict"] == "OK"


def test_bot_thinks_it_holds_btc_but_kraken_does_not():
    out = R.reconcile(pf_btc(0.002), bal(0.0005, 80), [stop_order()], simulation=False)
    assert status(out, "Position") == "BLOQUANT" and not out["pret_pour_le_reel"]


def test_missing_stop_blocks_and_low_stop_warns():
    assert status(R.reconcile(pf_btc(), bal(0.002), [], False), "Stop") == "BLOQUANT"
    out = R.reconcile(pf_btc(stop=40000), bal(0.002), [stop_order(trigger=35000)], False)
    assert status(out, "Stop") == "ATTENTION" and out["pret_pour_le_reel"]
    assert status(R.reconcile(pf_btc(0.002), bal(0.002), [stop_order(amount=0.001)], False), "Stop") == "BLOQUANT"


def test_orphan_stop_and_unknown_orders_warn():
    out = R.reconcile(pf_euros(10), bal(0, 50), [stop_order(), {"id": "L1", "side": "buy", "type": "limit",
                                                               "amount": 0.001, "price": 30000}], False)
    assert status(out, "Stop orphelin") == "ATTENTION" and status(out, "Autres ordres") == "ATTENTION"
    assert out["verdict"] == "ATTENTION"


def test_btc_outside_envelope_is_only_a_warning():
    out = R.reconcile(pf_euros(10), bal(0.01, 50), [], False)
    assert status(out, "BTC hors enveloppe") == "ATTENTION" and out["pret_pour_le_reel"]


def test_kraken_raw_stop_order_is_recognised():
    raw = {"id": "S2", "side": "sell", "type": "stop-loss", "amount": 0.002, "price": None,
           "info": {"descr": {"ordertype": "stop-loss", "price": "41000.0"}}}
    out = R.reconcile(pf_btc(stop=40000), bal(0.002), [raw], False)
    assert status(out, "Stop") == "OK"


@pytest.mark.parametrize("msg,expect", [("kraken {\"error\":[\"EGeneral:Permission denied\"]}", "Requête des fonds"),
                                        ("kraken EAPI:Invalid key", "clé API refusée"),
                                        ("timeout", "injoignable")])
def test_access_errors_are_explained(msg, expect):
    out = R.access_error(RuntimeError(msg))
    assert out["verdict"] == "BLOQUANT" and expect in out["controles"][0]["detail"]


def test_endpoint_requires_secret_and_never_orders(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("WEBHOOK_SECRET", "mot-de-passe-test")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    sys.modules.pop("app", None)
    import app as A
    cl = A.app.test_client()
    assert cl.get("/gardien/reconciliation").status_code == 401
    assert cl.get("/gardien/reconciliation?secret=faux").status_code == 401

    calls = []

    class FakeEx:
        def fetch_balance(self):
            calls.append("balance"); return bal(0, 2.0)

        def fetch_open_orders(self, symbol):
            calls.append(symbol); return []

        def __getattr__(self, name):  # toute autre méthode (create_order, cancel_order…) serait une faute
            raise AssertionError(f"appel interdit : {name}")

    monkeypatch.setattr(A, "_make_exchange", lambda: FakeEx())
    j = cl.get("/gardien/reconciliation?secret=mot-de-passe-test").get_json()
    assert calls == ["balance", "BTC/EUR"]
    assert j["verdict"] == "BLOQUANT" and j["kraken_dit"]["eur_disponible"] == 2.0 and j["ordres"].startswith("aucun")

    def boom():
        raise RuntimeError('kraken {"error":["EGeneral:Permission denied"]}')
    monkeypatch.setattr(A, "_make_exchange", boom)
    j = cl.get("/gardien/reconciliation", headers={"X-Webhook-Token": "mot-de-passe-test"}).get_json()
    assert "Requête des fonds" in j["controles"][0]["detail"]
