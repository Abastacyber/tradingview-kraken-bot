"""Notifications téléphone (ntfy) : jamais bloquantes, seulement sur changement, aucun envoi réel en test."""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import notify  # noqa: E402


def test_no_topic_no_send():
    called = []
    assert notify.send("t", "m", wait=True, opener=lambda *a, **k: called.append(1)) is False and not called


def test_payload_is_json_utf8(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "orion-test-sujet")
    sent = []

    def opener(req, timeout):
        sent.append(json.loads(req.data.decode("utf-8")))
        return io.BytesIO(b"{}")
    assert notify.send("🟢 ORION a ACHETÉ", "0,0003 BTC à 60 000 €", 4, ["green_circle"], wait=True, opener=opener)
    assert sent[0] == {"topic": "orion-test-sujet", "title": "🟢 ORION a ACHETÉ", "message": "0,0003 BTC à 60 000 €",
                       "priority": 4, "tags": ["green_circle"]}


def test_network_error_never_raises(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "x")

    def boom(*a, **k):
        raise OSError("réseau coupé")
    assert notify.send("t", "m", wait=True, opener=boom) is False


def test_messages():
    assert notify.for_operation({"type": "ACHAT (réel, limit)", "btc": 0.0003, "prix": 60000, "montant_eur": 20})[0].startswith("🟢")
    assert "Stop" in notify.for_operation({"type": "VENTE PAR STOP (réelle, ordre stop Kraken)"})[0]
    t = notify.for_operation({"type": "VENTE (réelle, limit)", "resultat_net_trade_pct": 12.5})
    assert t[0].startswith("🔵") and "+12.50 %" in t[1]
    assert notify.for_event("achat non placé : verrou fermé (voir la réconciliation)") is None  # pas de spam horaire
    assert notify.for_event("stop de protection posé chez Kraken à 40000.0 € pour 0.0003 BTC")[0].startswith("🛡️")
    assert notify.for_event("stop de protection NON posé : fonds insuffisants")[0].startswith("⚠️")


def _app(monkeypatch, tmp_path, dry_run="false", reel="true"):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("DRY_RUN", dry_run)
    monkeypatch.setenv("GARDIEN_REEL", reel)
    monkeypatch.setenv("WEBHOOK_SECRET", "mdp")
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    monkeypatch.setenv("GARDIEN_CAPITAL_EUR", "20")
    sys.modules.pop("app", None)
    import app as A
    A._gardien_checks.clear()
    A._gardien_cache.update({"ts": 0.0, "data": None, "candles": None})
    return A


def test_live_buy_and_reconciliation_changes_are_notified_once(monkeypatch, tmp_path):
    from test_gardien import series
    from test_gardien_reel import FakeKrakenAccount
    A = _app(monkeypatch, tmp_path)
    got = []
    monkeypatch.setattr(A.notify, "send", lambda *a, **k: got.append(a[0]) or True)
    ex = FakeKrakenAccount(eur=0.09, bid=400.0, ask=400.1)
    monkeypatch.setattr(A, "_make_exchange", lambda: ex)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    A._gardien_tick()
    assert any("BLOQUANT" in t for t in got)
    n = len(got)
    A._gardien_cache.update({"ts": 0.0}); A._gardien_tick()
    assert len(got) == n  # même verdict : pas de nouvelle notification
    ex.eur = 25.0  # dépôt : tout concorde, achat placé
    A._gardien_cache.update({"ts": 0.0}); A._gardien_tick()
    assert any("tout concorde" in t for t in got[n:])
    (oid,) = ex.orders
    ex.fill(oid)
    A._gardien_cache.update({"ts": 0.0}); A._gardien_tick()
    assert any(t.startswith("🟢 ORION a ACHETÉ") for t in got)


def test_signal_change_notified(monkeypatch, tmp_path):
    from test_gardien import series
    A = _app(monkeypatch, tmp_path, dry_run="true", reel="false")
    got = []
    monkeypatch.setattr(A.notify, "send", lambda *a, **k: got.append(a[0]) or True)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([400 - i for i in range(300)]))
    A._gardien_tick()
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(301)]))
    A._gardien_cache.update({"ts": 0.0}); A._gardien_tick()
    assert "📈 Signal du gardien : ACHETEUR" in got


def test_notif_test_endpoint(monkeypatch, tmp_path):
    A = _app(monkeypatch, tmp_path)
    cl = A.app.test_client()
    assert cl.get("/gardien/notif-test").status_code == 401
    assert cl.get("/gardien/notif-test?secret=mdp").get_json()["envoye"] is False  # NTFY_TOPIC absent
    monkeypatch.setenv("NTFY_TOPIC", "orion-test")
    monkeypatch.setattr(A.notify, "_post", lambda payload, opener=None: payload["title"] == "🔔 Test ORION")
    assert cl.get("/gardien/notif-test?secret=mdp").get_json()["envoye"] is True
    assert cl.get("/health").get_json()["notifications_telephone"] is True
