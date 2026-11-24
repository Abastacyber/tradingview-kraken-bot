"""Vrais ordres du gardien (préparés, verrouillés) et ORDRE-TEST — sans jamais contacter Kraken."""
import base64
import os
import sys
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_live as L  # noqa: E402

ccxt = pytest.importorskip("ccxt")

MARKET = {"id": "XXBTZEUR", "symbol": "BTC/EUR", "base": "BTC", "quote": "EUR", "baseId": "XXBT", "quoteId": "ZEUR",
          "altname": "XBTEUR", "type": "spot", "spot": True, "active": True,
          "precision": {"amount": 1e-8, "price": 0.1},
          "limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.45}, "price": {}}}


def real_ccxt_kraken(sent):
    """Le VRAI objet ccxt Kraken, mais la requête réseau est interceptée (rien n'est envoyé)."""
    ex = ccxt.kraken({"apiKey": "k", "secret": base64.b64encode(b"x" * 64).decode()})
    ex.set_markets([MARKET])
    ex.load_markets = lambda *a, **k: ex.markets
    ex.fetch_ticker = lambda s: {"bid": 56000.0, "ask": 56010.0}

    def fake_fetch(url, method="GET", headers=None, body=None):
        req = dict(urllib.parse.parse_qsl(body or ""))
        sent.append((url, req))
        kind = "stop loss" if req.get("ordertype") == "stop-loss" else "limit"
        descr = f"{req.get('type')} {req.get('volume')} XBTEUR @ {kind} {req.get('price')}"
        return {"error": [], "result": {"descr": {"order": descr}}}  # réponse Kraken en mode validate (pas de txid)
    ex.fetch = fake_fetch
    return ex


def test_ccxt_really_sends_validate_and_stop_loss_to_kraken():
    sent = []
    ex = real_ccxt_kraken(sent)
    L.send(ex, L.prepare_buy(ex, 15), validate=True)
    L.send(ex, L.prepare_stop(ex, 0.0002, 28000), validate=True)
    L.send(ex, L.prepare_sell(ex, 0.0002), validate=True)
    bodies = [b for url, b in sent if url.endswith("/AddOrder")]
    assert len(bodies) == 3 and all(b.get("validate") in ("true", "True") for b in bodies)
    buy, stop, sell = bodies
    assert buy["type"] == "buy" and buy["ordertype"] == "limit" and float(buy["price"]) == pytest.approx(56122.0, abs=0.2)
    assert float(buy["volume"]) * float(buy["price"]) * 1.008 <= 15.0001  # frais compris, jamais plus que le montant
    assert stop["type"] == "sell" and stop["ordertype"] == "stop-loss" and float(stop["price"]) == 28000
    assert sell["type"] == "sell" and sell["ordertype"] == "limit"


def test_minimums_are_enforced():
    ex = real_ccxt_kraken([])
    with pytest.raises(ValueError):
        L.prepare_buy(ex, 0.3)  # sous 0,45 € / 0,00006 BTC


class FakeEx:
    """Faux Kraken avec carnet, exécution immédiate et historique des appels."""
    def __init__(self, fail_on=None, buy_fills=True, sell_fills=True):
        self.calls, self.orders, self.fail_on = [], {}, fail_on or {}
        self.buy_fills, self.sell_fills, self.n = buy_fills, sell_fills, 0

    def load_markets(self):
        return {"BTC/EUR": MARKET}

    def fetch_ticker(self, s):
        return {"bid": 56000.0, "ask": 56010.0}

    def price_to_precision(self, s, p):
        return f"{round(p, 1)}"

    def amount_to_precision(self, s, a):
        return f"{int(a * 1e8) / 1e8:.8f}"

    def create_order(self, symbol, type, side, amount, price, params):
        kind = "stop" if "stopLossPrice" in params else side
        self.calls.append(("create", kind, amount, price, dict(params)))
        if kind in self.fail_on:
            raise RuntimeError(self.fail_on[kind])
        if params.get("validate"):
            return {"id": None}
        self.n += 1
        oid = f"O{self.n}"
        fills = {"buy": self.buy_fills, "sell": self.sell_fills, "stop": False}[kind]
        self.orders[oid] = {"id": oid, "status": "closed" if fills else "open", "filled": amount if fills else 0.0,
                            "cost": amount * (price or 56000.0) if fills else 0.0, "kind": kind}
        return {"id": oid}

    def fetch_order(self, oid, s):
        return self.orders[oid]

    def fetch_open_orders(self, s):
        return [o for o in self.orders.values() if o["status"] == "open"]

    def cancel_order(self, oid, s):
        self.calls.append(("cancel", oid))
        self.orders[oid]["status"] = "canceled"


def kinds(ex):
    return [(c[0], c[1]) for c in ex.calls]


def test_validation_mode_never_executes():
    ex = FakeEx()
    out = L.test_round_trip(ex, 15, real=False)
    assert out["resultat"] == "OK" and all(c[4].get("validate") for c in ex.calls if c[0] == "create")
    assert not ex.orders  # aucun ordre réel


def test_validation_insufficient_funds_is_ok_but_permission_is_not():
    out = L.test_round_trip(FakeEx(fail_on={"buy": "EOrder:Insufficient funds"}), 15, real=False)
    assert out["resultat"] == "OK" and "normal avant le dépôt" in out["etapes"][1]["detail"]
    out = L.test_round_trip(FakeEx(fail_on={"stop": "EGeneral:Permission denied"}), 15, real=False)
    assert out["resultat"] == "ÉCHEC" and "Créer et modifier" in out["etapes"][2]["detail"]


def test_amount_is_capped():
    out = L.test_round_trip(FakeEx(), 25, real=True)
    assert out["resultat"] == "REFUSÉ"


def test_real_round_trip_buy_stop_cancel_sell():
    ex = FakeEx()
    out = L.test_round_trip(ex, 15, real=True, sleep=lambda s: None)
    assert out["resultat"] == "OK"
    assert kinds(ex) == [("create", "buy"), ("create", "stop"), ("cancel", "O2"), ("create", "sell")]
    assert ex.orders["O2"]["status"] == "canceled"  # aucun stop laissé ouvert


def test_real_sell_failure_is_reported_not_hidden():
    ex = FakeEx(fail_on={"sell": "kraken error"})
    out = L.test_round_trip(ex, 15, real=True, sleep=lambda s: None)
    assert out["resultat"].startswith("ATTENTION") and "vendre" in out["a_faire"]


def test_unfilled_buy_is_cancelled():
    ex = FakeEx(buy_fills=False)
    out = L.test_round_trip(ex, 15, real=True, sleep=lambda s: None)
    assert out["resultat"].startswith("ÉCHEC (aucun BTC") and ("cancel", "O1") in ex.calls


def _app(monkeypatch, tmp_path, autorise="false"):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("WEBHOOK_SECRET", "mdp")
    monkeypatch.setenv("GARDIEN_ORDRE_TEST_AUTORISE", autorise)
    sys.modules.pop("app", None)
    import app as A
    return A


def test_endpoint_locks(monkeypatch, tmp_path):
    A = _app(monkeypatch, tmp_path)
    fake = FakeEx()
    monkeypatch.setattr(A, "_make_exchange", lambda: fake)
    cl = A.app.test_client()
    assert cl.get("/gardien/ordre-test").status_code == 401
    j = cl.get("/gardien/ordre-test?secret=mdp").get_json()  # validation par défaut
    assert j["resultat"] == "OK" and not fake.orders
    r = cl.get("/gardien/ordre-test?secret=mdp&mode=reel&confirmer=OUI")
    assert r.status_code == 403 and "GARDIEN_ORDRE_TEST_AUTORISE" in r.get_json()["detail"] and not fake.orders

    A = _app(monkeypatch, tmp_path, autorise="true")
    monkeypatch.setattr(A, "_make_exchange", lambda: fake)
    A.gardien_live.time.sleep = lambda s: None
    cl = A.app.test_client()
    assert cl.get("/gardien/ordre-test?secret=mdp&mode=reel").status_code == 400  # sans confirmer=OUI
    assert cl.get("/gardien/ordre-test?secret=mdp&mode=reel&confirmer=OUI").get_json()["resultat"] == "OK"
    assert cl.get("/gardien/ordre-test?secret=mdp&mode=reel&confirmer=OUI").status_code == 409  # pas 2 fois en 10 min
