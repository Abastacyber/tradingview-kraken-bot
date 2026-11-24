"""GARDIEN RÉEL (étape 7) : scénarios complets contre un faux Kraken. Aucun appel réseau."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien_live as L  # noqa: E402

DAY = 86400
T0 = 1_780_000_000 - 1_780_000_000 % DAY
MARKET = {"limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.45}}}


class FakeKraken:
    def __init__(self, bid=50000.0, ask=50010.0):
        self.bid, self.ask, self.orders, self.log, self.n, self.fail = bid, ask, {}, [], 0, {}

    # marché
    def load_markets(self): return {"BTC/EUR": MARKET}
    def fetch_ticker(self, s): return {"bid": self.bid, "ask": self.ask}
    def price_to_precision(self, s, p): return f"{round(p, 1)}"
    def amount_to_precision(self, s, a): return f"{int(a * 1e8) / 1e8:.8f}"

    # ordres
    def create_order(self, symbol, type, side, amount, price, params):
        kind = "stop" if "stopLossPrice" in params else side
        if kind in self.fail:
            raise RuntimeError(self.fail.pop(kind))
        assert not params.get("validate")
        self.n += 1
        oid = f"K{self.n}"
        self.orders[oid] = {"id": oid, "kind": kind, "side": side, "amount": amount, "price": price,
                            "trigger": params.get("stopLossPrice"), "postOnly": params.get("postOnly", False),
                            "status": "open", "filled": 0.0, "cost": 0.0, "fee": None}
        self.log.append(("create", kind, oid))
        return {"id": oid}

    def fill(self, oid, qty=None, price=None, fee_rate=0.004):
        o = self.orders[oid]
        qty = o["amount"] if qty is None else qty
        px = price or o["price"] or o["trigger"]
        o["filled"] += qty
        o["cost"] += qty * px
        o["fee"] = {"cost": o["cost"] * fee_rate, "currency": "EUR"}
        o["average"] = o["cost"] / o["filled"]
        if o["filled"] >= o["amount"] - 1e-12:
            o["status"] = "closed"

    def fetch_order(self, oid, s): return dict(self.orders[oid])

    def cancel_order(self, oid, s):
        self.log.append(("cancel", oid))
        if self.orders[oid]["status"] == "open":
            self.orders[oid]["status"] = "canceled"

    def open_ids(self, kind=None):
        return [o["id"] for o in self.orders.values() if o["status"] == "open" and (kind is None or o["kind"] == kind)]


def candles(n, start_price=100.0):
    return [(T0 + i * DAY, start_price, start_price * 1.01, start_price * 0.99, start_price) for i in range(n)]


def sig(i, long, buy_ok=None):
    buy_ok = long if buy_ok is None else buy_ok
    return {"jour_cloture_utc": L.time.strftime("%Y-%m-%d", L.time.gmtime(T0 + i * DAY)),
            "position_theorique": "ACHETEUR (BTC)" if long else "EN EUROS",
            "ema20_au_dessus_ema50": buy_ok, "prix_au_dessus_ema200": buy_ok}


def step(ex, pf, i, long, cds=None, atr=1000.0, can_open=True, can_protect=True, hours=1.0, saves=None, **kw):
    cds = cds if cds is not None else [(T0 + j * DAY, 50000.0, 51000.0, 49000.0, 50000.0) for j in range(i + 1)]
    return L.live_step(ex, pf, sig(i, long), cds, [atr] * len(cds), 12.0, can_open, can_protect,
                       now=T0 + (i + 1) * DAY + hours * 3600, save=(saves.append if saves is not None else None), **kw)


def test_full_life_cycle_buy_stop_raise_sell():
    ex, pf, saves = FakeKraken(), L.new_live_portfolio(100.0), []
    # J0 : signal d'achat → achat limit (maker) au bid, enregistré tout de suite
    step(ex, pf, 0, True, saves=saves)
    buy_id = pf["ordre_en_cours"]["id"]
    assert ex.orders[buy_id]["postOnly"] and ex.orders[buy_id]["price"] == 50000.0
    assert saves and saves[-1]["ordre_en_cours"]["id"] == buy_id
    assert ex.orders[buy_id]["amount"] * 50000 * 1.004 <= 100.0001
    # exécuté chez Kraken → au tick suivant, portefeuille mis à jour (une seule fois)
    ex.fill(buy_id)
    ops = step(ex, pf, 0, True, hours=2)
    assert ops[0]["type"] == "ACHAT (réel, limit)" and pf["en_position"] and pf["ordre_en_cours"] is None
    assert pf["euros"] < 1.0 and pf["btc"] == pytest.approx(ex.orders[buy_id]["amount"])
    assert not ex.open_ids("stop")  # pas de stop avant le premier jour clôturé après l'achat
    # J1 clôturé : stop = plus haut 51000 − 12 × 1000 = 39000, posé chez Kraken
    step(ex, pf, 1, True)
    (stop1,) = ex.open_ids("stop")
    assert ex.orders[stop1]["trigger"] == 39000.0 and ex.orders[stop1]["amount"] == pytest.approx(pf["btc"])
    # J2 : nouveau plus haut 60000 → stop 48000 : l'ancien est retiré, le nouveau posé (jamais deux à la fois)
    cds = [(T0 + j * DAY, 50000.0, 51000.0 if j < 2 else 60000.0, 49000.0, 50000.0) for j in range(3)]
    step(ex, pf, 2, True, cds=cds)
    (stop2,) = ex.open_ids("stop")
    assert stop2 != stop1 and ex.orders[stop2]["trigger"] == 48000.0
    # J3 : le prix baisse, le stop ne redescend pas
    cds.append((T0 + 3 * DAY, 50000.0, 50500.0, 47000.0, 49000.0))
    step(ex, pf, 3, True, cds=cds, atr=500.0)
    assert ex.open_ids("stop") == [stop2] or ex.orders[ex.open_ids("stop")[0]]["trigger"] >= 48000.0
    # J4 : signal de vente → stop retiré, vente limit au ask
    cds.append((T0 + 4 * DAY, 49000.0, 49500.0, 48500.0, 49000.0))
    step(ex, pf, 4, False, cds=cds)
    assert not ex.open_ids("stop")
    sell_id = pf["ordre_en_cours"]["id"]
    assert ex.orders[sell_id]["side"] == "sell" and ex.orders[sell_id]["price"] == 50010.0
    ex.fill(sell_id)
    ops = step(ex, pf, 4, False, cds=cds, hours=2)
    assert ops[0]["type"] == "VENTE (réelle, limit)" and not pf["en_position"] and pf["euros"] > 99.0
    assert ops[0]["resultat_net_trade_pct"] == pytest.approx((pf["euros"] / 100.0 - 1) * 100, abs=0.05)
    assert not ex.open_ids()  # plus aucun ordre ouvert chez Kraken


def test_unfilled_maker_is_repriced_then_taker_after_12h():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True, hours=0.1)
    first = pf["ordre_en_cours"]["id"]
    ex.bid, ex.ask = 51000.0, 51010.0
    step(ex, pf, 0, True, hours=1.1)
    second = pf["ordre_en_cours"]["id"]
    assert ex.orders[first]["status"] == "canceled" and ex.orders[second]["price"] == 51000.0 and not pf["ordre_en_cours"]["taker"]
    step(ex, pf, 0, True, hours=12.5)  # délai dépassé → ordre immédiat au ask (+0,2 %)
    third = pf["ordre_en_cours"]
    assert third["taker"] and ex.orders[third["id"]]["price"] == pytest.approx(51010.0 * 1.002, abs=0.1)
    assert len(ex.open_ids()) == 1  # jamais deux achats ouverts en même temps


def test_partial_fill_counts_once_and_rest_is_replaced():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True)
    oid = pf["ordre_en_cours"]["id"]
    half = ex.orders[oid]["amount"] / 2
    ex.fill(oid, qty=half)
    step(ex, pf, 0, True, hours=2)
    assert pf["btc"] == pytest.approx(half) and pf["en_position"] and 45 < pf["euros"] < 55
    rest = pf["ordre_en_cours"]["id"]
    ex.fill(rest)
    step(ex, pf, 0, True, hours=3)
    assert pf["ordre_en_cours"] is None and pf["euros"] < 1.0
    assert len([t for t in pf["trades"] if t["type"].startswith("ACHAT")]) == 2


def test_stop_executed_by_kraken_closes_position():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True); ex.fill(pf["ordre_en_cours"]["id"]); step(ex, pf, 0, True, hours=2)
    step(ex, pf, 1, True)
    (sid,) = ex.open_ids("stop")
    ex.fill(sid, price=38900.0, fee_rate=0.008)  # krach : Kraken exécute le stop pendant la nuit
    cds = [(T0 + j * DAY, 50000.0, 51000.0, 49000.0, 50000.0) for j in range(2)] + [(T0 + 2 * DAY, 45000, 45000, 38000, 39000)]
    ops = step(ex, pf, 2, True, cds=cds)
    assert ops[0]["type"].startswith("VENTE PAR STOP") and not pf["en_position"] and pf["stop_order_id"] is None
    assert ops[0]["resultat_net_trade_pct"] < -20


def test_lock_closed_blocks_buy_but_allows_protection():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True, can_open=False)
    assert not ex.orders and "verrou fermé" in pf["evenements"][-1]["detail"]
    assert pf["dernier_jour_traite"] is None  # sera retenté à la prochaine vérification
    step(ex, pf, 0, True, hours=2)  # verrou rouvert
    ex.fill(pf["ordre_en_cours"]["id"]); step(ex, pf, 0, True, hours=3)
    step(ex, pf, 1, True, can_open=False)  # le stop se pose quand même (protection)
    assert ex.open_ids("stop")
    step(ex, pf, 2, False, can_open=False)  # la vente se fait quand même (protection)
    assert pf["ordre_en_cours"]["side"] == "sell"
    step(ex, pf, 3, False, can_open=False, can_protect=False)  # Kraken illisible : on ne touche à rien
    assert pf["ordre_en_cours"] is None or ex.orders[pf["ordre_en_cours"]["id"]]["side"] == "sell"


def test_kraken_refuses_order_nothing_pending_and_retry():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    ex.fail["buy"] = "EOrder:Insufficient funds"
    step(ex, pf, 0, True)
    assert pf["ordre_en_cours"] is None and "fonds insuffisants" in pf["evenements"][-1]["detail"]
    step(ex, pf, 0, True, hours=2)
    assert pf["ordre_en_cours"] is not None


def test_stop_cancelled_outside_orion_is_replaced():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True); ex.fill(pf["ordre_en_cours"]["id"]); step(ex, pf, 0, True, hours=2)
    step(ex, pf, 1, True)
    (sid,) = ex.open_ids("stop")
    ex.orders[sid]["status"] = "canceled"  # annulé à la main sur Kraken
    step(ex, pf, 1, True, hours=3)
    (sid2,) = ex.open_ids("stop")
    assert sid2 != sid and "disparu" in " ".join(e["detail"] for e in pf["evenements"])


def test_one_decision_per_day_no_double_buy():
    ex, pf = FakeKraken(), L.new_live_portfolio(100.0)
    step(ex, pf, 0, True); ex.fill(pf["ordre_en_cours"]["id"])
    for h in (2, 3, 4, 5):
        step(ex, pf, 0, True, hours=h)
    assert len([o for o in ex.orders.values() if o["kind"] == "buy"]) == 1


class FakeKrakenAccount(FakeKraken):
    def __init__(self, eur=150.0, **kw):
        super().__init__(**kw)
        self.eur = eur

    def fetch_balance(self):
        btc = sum(o["filled"] for o in self.orders.values() if o["side"] == "buy") - \
            sum(o["filled"] for o in self.orders.values() if o["side"] == "sell")
        return {"total": {"BTC": btc, "EUR": self.eur}, "free": {"BTC": btc, "EUR": self.eur}}

    def fetch_open_orders(self, s):
        return [{"id": o["id"], "side": o["side"], "amount": o["amount"], "type": "stop-loss" if o["kind"] == "stop" else "limit",
                 "triggerPrice": o["trigger"]} for o in self.orders.values() if o["status"] == "open"]


def _app(monkeypatch, tmp_path, dry_run, reel):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("DRY_RUN", dry_run)
    monkeypatch.setenv("GARDIEN_REEL", reel)
    monkeypatch.setenv("WEBHOOK_SECRET", "mdp")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    monkeypatch.setenv("GARDIEN_CAPITAL_EUR", "100")
    sys.modules.pop("app", None)
    import app as A
    A._gardien_checks.clear()
    A._gardien_cache.update({"ts": 0.0, "data": None, "candles": None})
    return A


@pytest.mark.parametrize("dry_run,reel", [("true", "true"), ("false", "false"), ("true", "false")])
def test_no_real_order_unless_both_locks_open(monkeypatch, tmp_path, dry_run, reel):
    from test_gardien import series
    A = _app(monkeypatch, tmp_path, dry_run, reel)
    ex = FakeKrakenAccount()
    monkeypatch.setattr(A, "_make_exchange", lambda: ex)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    e = A._gardien_tick()
    assert not ex.orders and "ordres_reels" not in e
    assert A.app.test_client().get("/health").get_json()["gardien_mode"] == "simulation"


def test_live_mode_places_real_buy_and_records_it(monkeypatch, tmp_path):
    from test_gardien import series
    A = _app(monkeypatch, tmp_path, "false", "true")
    ex = FakeKrakenAccount(eur=150.0, bid=400.0, ask=400.1)
    monkeypatch.setattr(A, "_make_exchange", lambda: ex)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    e = A._gardien_tick()
    assert e["reconciliation"] == "OK" and len(ex.orders) == 1
    (o,) = ex.orders.values()
    assert o["side"] == "buy" and o["postOnly"] and o["amount"] * o["price"] * 1.004 <= 100.0001
    cl = A.app.test_client()
    assert cl.get("/health").get_json()["gardien_mode"].startswith("RÉEL")
    assert cl.get("/gardien/portefeuille-reel").status_code == 401
    pr = cl.get("/gardien/portefeuille-reel?secret=mdp").get_json()
    assert pr["portefeuille"]["ordre_en_cours"]["id"] == o["id"]
    # tick suivant : l'achat est exécuté → enregistré, pas de second achat
    ex.fill(o["id"])
    A._gardien_cache.update({"ts": 0.0})
    A._gardien_tick()
    pf = cl.get("/gardien/portefeuille-reel?secret=mdp").get_json()["portefeuille"]
    assert pf["en_position"] and pf["ordre_en_cours"] is None and len(ex.orders) == 1


def test_live_mode_refuses_to_buy_when_envelope_not_funded(monkeypatch, tmp_path):
    from test_gardien import series
    A = _app(monkeypatch, tmp_path, "false", "true")
    ex = FakeKrakenAccount(eur=0.09)
    monkeypatch.setattr(A, "_make_exchange", lambda: ex)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    e = A._gardien_tick()
    assert e["reconciliation"] == "BLOQUANT" and not ex.orders and "Euros" in e["verrou"]
