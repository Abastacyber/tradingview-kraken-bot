"""
ORION LAB — taux de financement (« funding ») des contrats perpétuels BTC.

Le funding est payé toutes les 8 h entre acheteurs et vendeurs de contrats perpétuels.
Positif et élevé = beaucoup de traders achètent avec effet de levier (excès d'optimisme).
Source : archives publiques Binance (BTCUSDT perpétuel, le plus gros marché ; indicateur
de sentiment global, valable aussi pour BTC/EUR).
"""
from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip"


def load(sym: str = "BTCUSDT", start=(2020, 1), end=(2026, 10)) -> pd.Series:
    """Funding journalier = somme des 3 paiements de la journée (en fraction, ex. 0.0003 = 0,03 %)."""
    parts = []
    y, m = start
    while (y, m) <= end:
        cache = os.path.join(HERE, "data", "funding", sym, f"{y}-{m:02d}.csv")
        if not os.path.exists(cache):
            try:
                with urllib.request.urlopen(URL.format(sym=sym, y=y, m=m), timeout=60) as r:
                    raw = r.read()
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    df = pd.read_csv(z.open(z.namelist()[0]))
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                df.to_csv(cache, index=False)
            except Exception as e:
                print(f"  - funding {y}-{m:02d} indisponible ({e.__class__.__name__})", file=sys.stderr)
        if os.path.exists(cache):
            parts.append(pd.read_csv(cache))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    df = pd.concat(parts, ignore_index=True)
    tcol = [c for c in df.columns if "time" in c.lower()][0]
    rcol = [c for c in df.columns if "rate" in c.lower() and "time" not in c.lower()][0]
    ts = pd.to_datetime(df[tcol].astype("int64"), unit="ms", utc=True)
    s = pd.Series(df[rcol].astype(float).values, index=ts).sort_index()
    return s.resample("1D").sum()
