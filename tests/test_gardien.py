"""Tests du gardien journalier (étape 1 : lecture seule)."""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import gardien  # noqa: E402

DAY = 86400


def series(prices, start=1_600_000_000 - 1_600_000_000 % DAY):
    return [(start + i * DAY, float(p)) for i, p in enumerate(prices)]


def test_ema_matches_pandas():
    pd = pytest.importorskip("pandas")
    vals = [100 + (i % 17) * 3 - (i % 5) for i in range(400)]
    ref = pd.Series(vals).ewm(span=50, adjust=False).mean().tolist()
    assert gardien.ema(vals, 50) == pytest.approx(ref, rel=1e-12)


def test_uptrend_is_buyer():
    sig = gardien.compute_signal(series([100 + i for i in range(300)]))
    assert sig["position_theorique"] == "ACHETEUR (BTC)"


def test_downtrend_stays_in_euros_and_explains():
    sig = gardien.compute_signal(series([400 - i for i in range(300)]))
    assert sig["position_theorique"] == "EN EUROS"
    assert "EMA200" in sig["explication"] and "EMA50" in sig["explication"]


def test_hold_when_price_dips_below_ema200_but_fast_above_slow():
    # longue hausse (achat), puis courte baisse sous l'EMA200 sans croisement baissier EMA20/EMA50
    prices = [100 + i for i in range(260)] + [250]  # une seule journée de forte baisse
    sig = gardien.compute_signal(series(prices))
    assert sig["prix_au_dessus_ema200"] is False
    assert sig["ema20_au_dessus_ema50"] is True
    assert sig["position_theorique"] == "ACHETEUR (BTC)"  # on garde : la vente exige EMA20 < EMA50


def test_too_short_history_raises():
    with pytest.raises(ValueError):
        gardien.compute_signal(series([100] * 100))


def test_unclosed_candle_is_dropped():
    rows = [[1_700_000_000 + i * DAY, "1", "1", "1", str(100 + i), "1", "1", 1] for i in range(3)]
    body = json.dumps({"error": [], "result": {"XXBTZEUR": rows, "last": 0}}).encode()
    now = rows[-1][0] + 3600  # la dernière bougie est en cours
    out = gardien.fetch_daily_candles(now=now, opener=lambda *a, **k: io.BytesIO(body))
    assert [c for _, c in out] == [100.0, 101.0]


def test_parity_with_lab_on_real_btc_history():
    """Le gardien et le laboratoire doivent prendre exactement les mêmes décisions."""
    pd = pytest.importorskip("pandas")
    sys.path.insert(0, os.path.join(ROOT, "lab"))
    import data as lab_data
    import strategies as S
    cache = os.path.join(ROOT, "lab", "data", "BTCEUR", "1h")
    if not os.path.isdir(cache):
        pytest.skip("historique du laboratoire non téléchargé")
    d = lab_data.resample(lab_data.load("1h"), "1D")
    candles = [(int(ts.timestamp()), float(c)) for ts, c in d.close.items()]
    g = gardien.compute_signal(candles)
    e20, e50, e200 = (S.ema(d.close, n).iloc[-1] for n in (20, 50, 200))
    assert g["ema20"] == pytest.approx(e20, abs=0.01)
    assert g["ema50"] == pytest.approx(e50, abs=0.01)
    assert g["ema200"] == pytest.approx(e200, abs=0.01)
    # position théorique = simulation « état » du labo (entrées/sorties sur signal)
    up, dn = S.ema_state(d, 20, 50, 200)
    in_pos = False
    for i in range(len(d)):
        if i < gardien.TREND:
            continue
        if not in_pos and up.iloc[i]:
            in_pos = True
        elif in_pos and dn.iloc[i]:
            in_pos = False
    assert (g["position_theorique"] == "ACHETEUR (BTC)") == in_pos


def test_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("RESTORE_ON_START", "false")
    sys.modules.pop("app", None)
    import app as A
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    A._gardien_cache.update({"ts": 0.0, "data": None})
    r = A.app.test_client().get("/gardien/signal")
    assert r.status_code == 200
    assert r.get_json()["position_theorique"] == "ACHETEUR (BTC)"
    assert r.get_json()["ordres"].startswith("aucun")


def test_endpoint_shows_euro_sign(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([400 - i for i in range(300)]))
    A._gardien_cache.update({"ts": 0.0, "data": None})
    body = A.app.test_client().get("/gardien/signal").get_data(as_text=True)
    assert "€" in body and "\\u20ac" not in body


def test_daily_history_matches_signal_and_flags_change():
    prices = [400 - i for i in range(260)] + [140 + 3 * i for i in range(60)]  # baisse puis forte hausse
    candles = series(prices)
    hist = gardien.daily_history(candles, 60)
    sig = gardien.compute_signal(candles)
    assert hist[0]["jour_cloture_utc"] == sig["jour_cloture_utc"]
    assert hist[0]["position"] == sig["position_theorique"] == "ACHETEUR (BTC)"
    assert sum(h["changement"] for h in hist) == 1  # un seul passage en euros -> BTC sur la période


def _fresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    sys.modules.pop("app", None)
    import app as A
    A._gardien_cache.update({"ts": 0.0, "data": None, "candles": None})
    A._gardien_checks.clear()
    return A


def test_clock_tick_detects_change(monkeypatch, tmp_path):
    A = _fresh_app(monkeypatch, tmp_path)
    assert A._gardien_clock_started is False  # horloge désactivée pendant les tests
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([400 - i for i in range(300)]))
    first = A._gardien_tick()
    assert first["position"] == "EN EUROS" and first["evenement"] == "nouveau jour clôturé"
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(301)]))
    second = A._gardien_tick()
    assert second["evenement"].startswith("CHANGEMENT")


def test_clock_tick_survives_kraken_error(monkeypatch, tmp_path):
    A = _fresh_app(monkeypatch, tmp_path)
    def boom(pair):
        raise RuntimeError("Kraken indisponible")
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", boom)
    assert "erreur" in A._gardien_tick()


def test_journal_endpoint(monkeypatch, tmp_path):
    A = _fresh_app(monkeypatch, tmp_path)
    monkeypatch.setattr(A.gardien, "fetch_daily_candles", lambda pair: series([100 + i for i in range(300)]))
    j = A.app.test_client().get("/gardien/journal").get_json()
    assert len(j["derniers_jours"]) == 30
    assert j["ordres"].startswith("aucun")
