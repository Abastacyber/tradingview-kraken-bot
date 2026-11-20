"""
ORION GARDIEN v1 — signal de tendance journalier (étape 1 : LECTURE SEULE).

Règle validée dans le laboratoire (EXP02/EXP03, voir docs/LAB_JOURNAL.md) :
  - ACHAT  : EMA20 > EMA50 ET clôture > EMA200 (bougies journalières BTC/EUR Kraken)
  - VENTE  : EMA20 < EMA50
  - entre les deux : on garde la position précédente.

Ce module ne passe AUCUN ordre. Il calcule seulement la position que le gardien
devrait avoir, à partir des bougies journalières CLÔTURÉES de Kraken.

Pas de dépendance lourde (pas de pandas) : calculs en Python pur. Un test vérifie
que les résultats sont identiques à ceux du laboratoire (tests/test_gardien.py).
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable, List, Optional, Tuple

FAST, SLOW, TREND = 20, 50, 200
DAY_SEC = 86400
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"
MIN_CANDLES = TREND + 50  # assez d'historique pour que l'EMA200 soit fiable

Candle = Tuple[int, float]  # (horodatage d'ouverture UTC en secondes, clôture)


def ema(values: List[float], span: int) -> List[float]:
    """Moyenne mobile exponentielle (identique à pandas ewm(span, adjust=False))."""
    alpha = 2.0 / (span + 1.0)
    out: List[float] = []
    e: Optional[float] = None
    for v in values:
        e = v if e is None else alpha * v + (1.0 - alpha) * e
        out.append(e)
    return out


def fetch_daily_candles(pair: str = "XBTEUR", now: Optional[float] = None,
                        opener: Callable = urllib.request.urlopen) -> List[Candle]:
    """Bougies journalières Kraken, en ne gardant que celles qui sont CLÔTURÉES."""
    with opener(KRAKEN_OHLC_URL.format(pair=pair), timeout=20) as r:
        payload = json.load(r)
    if payload.get("error"):
        raise RuntimeError(f"Kraken OHLC: {payload['error']}")
    key = next(k for k in payload["result"] if k != "last")
    now = time.time() if now is None else now
    candles = [(int(row[0]), float(row[4])) for row in payload["result"][key]]
    return [c for c in candles if c[0] + DAY_SEC <= now]


def _replay(candles: List[Candle]):
    """Rejoue la règle jour par jour. Renvoie les EMA et la position après chaque clôture."""
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"historique insuffisant ({len(candles)} bougies, {MIN_CANDLES} nécessaires)")
    closes = [c for _, c in candles]
    ef, es, et = ema(closes, FAST), ema(closes, SLOW), ema(closes, TREND)
    positions: List[bool] = []
    in_pos = False
    last_change: Optional[int] = None
    for i, (ts, close) in enumerate(candles):
        if i >= TREND:  # avant : période de chauffe de l'EMA200
            if not in_pos and ef[i] > es[i] and close > et[i]:
                in_pos, last_change = True, ts
            elif in_pos and ef[i] < es[i]:
                in_pos, last_change = False, ts
        positions.append(in_pos)
    return ef, es, et, positions, last_change


def daily_history(candles: List[Candle], days: int = 30) -> List[dict]:
    """Journal des décisions des derniers jours (du plus récent au plus ancien)."""
    ef, es, et, pos, _ = _replay(candles)
    out = []
    for i in range(len(candles) - 1, max(len(candles) - 1 - days, 0), -1):
        ts, close = candles[i]
        out.append({
            "jour_cloture_utc": time.strftime("%Y-%m-%d", time.gmtime(ts)),
            "cloture": round(close, 2), "ema20": round(ef[i], 2), "ema50": round(es[i], 2), "ema200": round(et[i], 2),
            "position": "ACHETEUR (BTC)" if pos[i] else "EN EUROS",
            "changement": pos[i] != pos[i - 1],
        })
    return out


def compute_signal(candles: List[Candle]) -> dict:
    """Rejoue la règle sur tout l'historique et renvoie l'état du gardien au dernier jour clôturé."""
    ef, es, et, positions, last_change = _replay(candles)
    in_pos = positions[-1]
    ts, close = candles[-1]
    f, s, t = ef[-1], es[-1], et[-1]
    buy_ok = f > s and close > t
    day = time.strftime("%Y-%m-%d", time.gmtime(ts))
    if in_pos:
        explication = ("Tendance haussière : le gardien est (ou serait) investi en BTC. "
                       "Il revendra quand l'EMA20 repassera sous l'EMA50.")
    elif buy_ok:  # ne devrait pas arriver : l'achat est déclenché dès que les conditions sont réunies
        explication = "Conditions d'achat réunies."
    else:
        manque = []
        if not f > s:
            manque.append(f"EMA20 ({f:,.0f} €) doit repasser au-dessus de l'EMA50 ({s:,.0f} €)")
        if not close > t:
            manque.append(f"le prix ({close:,.0f} €) doit repasser au-dessus de l'EMA200 ({t:,.0f} €)")
        explication = "Le gardien reste en euros. Pour acheter : " + " et ".join(manque) + "."
    return {
        "regle": f"ORION GARDIEN v1 — EMA{FAST}/EMA{SLOW} + filtre EMA{TREND}, journalier",
        "jour_cloture_utc": day,
        "cloture": round(close, 2),
        "ema20": round(f, 2),
        "ema50": round(s, 2),
        "ema200": round(t, 2),
        "ema20_au_dessus_ema50": f > s,
        "prix_au_dessus_ema200": close > t,
        "position_theorique": "ACHETEUR (BTC)" if in_pos else "EN EUROS",
        "dernier_changement_utc": time.strftime("%Y-%m-%d", time.gmtime(last_change)) if last_change else None,
        "explication": explication.replace(",", " "),
        "bougies_utilisees": len(candles),
        "ordres": "aucun (étape 1 : lecture seule)",
    }
