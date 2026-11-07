"""
Tests hors-ligne du sizing DRY_RUN (aucun appel réseau, aucune clé réelle).

Un faux exchange remplace ccxt.kraken : prix BTC/EUR fixe, minimum Kraken
0.00005 BTC, et un solde EUR réel volontairement minuscule (2 €) pour
reproduire le bug du 22/08 : quote=25 € -> 0.00005 BTC.
"""
import importlib
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PRICE = 60_000.0
SECRET = "test-secret"


class FakeKraken:
    def __init__(self, free_eur=2.0):
        self.free_eur = free_eur
        self.orders = []

    def load_markets(self):
        return {
            "BTC/EUR": {
                "limits": {"amount": {"min": 0.00005}, "cost": {"min": 0.5}},
                "precision": {"amount": 1e-08},
                "info": {},
            }
        }

    def fetch_ticker(self, symbol):
        return {"last": PRICE}

    def fetch_free_balance(self):
        return {"EUR": self.free_eur, "BTC": 0.0}

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.8f}"

    def create_market_buy_order(self, *a, **k):  # ne doit jamais être appelé en DRY_RUN
        raise AssertionError("ordre réel interdit en DRY_RUN")

    create_market_sell_order = create_market_buy_order
    create_order = create_market_buy_order


def load_app(monkeypatch, **env):
    base = {
        "DRY_RUN": "true",
        "WEBHOOK_SECRET": SECRET,
        "KRAKEN_API_KEY": "dummy",
        "KRAKEN_API_SECRET": "dummy",
        "TRAILING_ENABLED": "false",
        "RESTORE_ON_START": "false",
        "BUY_COOL_SEC": "0",
        "FEE_BUFFER_PCT": "0",
    }
    base.update(env)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STATE_FILE", os.path.join(env.get("_tmp", "/tmp"), "orion_test_state.json"))
    sys.modules.pop("app", None)
    mod = importlib.import_module("app")
    fake = FakeKraken(free_eur=float(env.get("_free_eur", 2.0)))
    monkeypatch.setattr(mod, "_make_exchange", lambda: fake)
    mod._load_markets.cache_clear()
    return mod, fake


def payload(signal, alert_id, **extra):
    p = {
        "protocol": "orion-v1",
        "signal": signal,
        "alert_id": alert_id,
        "symbol": "BTC/EUR",
        "timeframe": "15",
        "confidence": 70,
        "price": PRICE,
        "sent_at": int(time.time() * 1000),
    }
    if signal == "BUY":
        p["stop_price"] = PRICE * 0.97
    p.update(extra)
    return p


def post(client, body):
    return client.post("/webhook", json=body, headers={"X-Webhook-Token": SECRET})


@pytest.fixture
def orion(monkeypatch, tmp_path):
    mod, fake = load_app(monkeypatch, _tmp=str(tmp_path))
    return mod, fake, mod.app.test_client()


def test_dry_run_buy_ignores_real_balance(orion):
    _, _, client = orion
    r = post(client, payload("BUY", "t-buy-1", quote=25))
    data = r.get_json()
    assert r.status_code == 200, data
    expected = 25 / PRICE  # ≈ 0.00041667
    assert data["amount"] == pytest.approx(expected, rel=1e-3)
    assert data["amount"] > 0.00005 * 2  # plus bloqué au minimum Kraken
    assert data["quote_used"] == pytest.approx(25)
    assert data["sizing_source"] == "dry_run_virtual"


def test_dry_run_quote_capped(monkeypatch, tmp_path):
    mod, _ = load_app(monkeypatch, _tmp=str(tmp_path), DRY_RUN_MAX_QUOTE="40")
    client = mod.app.test_client()
    data = post(client, payload("BUY", "t-buy-cap", quote=500)).get_json()
    assert data["quote_used"] == pytest.approx(40)
    assert data["amount"] == pytest.approx(40 / PRICE, rel=1e-3)


def test_dry_run_full_cycle_buy_sell(orion):
    _, _, client = orion
    assert post(client, payload("BUY", "t-cyc-buy", quote=25)).status_code == 200
    h = client.get("/health").get_json()
    assert h["has_position"] is True and h["position_id"]
    r = post(client, payload("SELL", "t-cyc-sell"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["side"] == "sell-close-long"
    h = client.get("/health").get_json()
    assert h["has_position"] is False and h["position_id"] is None


def test_live_still_uses_real_balance(monkeypatch, tmp_path):
    """Hors DRY_RUN, le solde réel reste la limite (aucun ordre n'est passé ici :
    2 € < minimum -> on vérifie juste que le solde réel est bien consulté)."""
    mod, fake = load_app(monkeypatch, _tmp=str(tmp_path), DRY_RUN="false", _free_eur="0")
    client = mod.app.test_client()
    r = post(client, payload("BUY", "t-live-1", quote=25))
    assert r.status_code == 400
    assert r.get_json()["error"] == "insufficient_quote"
