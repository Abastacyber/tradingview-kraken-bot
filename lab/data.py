"""
ORION LAB — données historiques BTC/EUR.

Source : archives publiques Binance (data.binance.vision), paire BTCEUR.
Pourquoi pas Kraken ? L'API publique Kraken ne renvoie que les 720 dernières
bougies (≈ 7 jours en 15 min). Pour un backtest sur plusieurs années, on
utilise l'historique Binance : le prix BTC/EUR est quasi identique entre
exchanges (arbitrage), et les FRAIS simulés restent ceux de Kraken.

Les fichiers sont mis en cache dans lab/data/ (non versionné).
"""
from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile
from datetime import date

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines/{sym}/{tf}/{sym}-{tf}-{y}-{m:02d}.zip"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]


def _months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _download_month(sym: str, tf: str, y: int, m: int) -> pd.DataFrame | None:
    cache = os.path.join(DATA_DIR, sym, tf, f"{y}-{m:02d}.csv")
    if os.path.exists(cache):
        return pd.read_csv(cache)
    url = BASE_URL.format(sym=sym, tf=tf, y=y, m=m)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read()
    except Exception as e:  # mois absent (avant listing) ou pas encore publié
        print(f"  - {y}-{m:02d} indisponible ({e.__class__.__name__})", file=sys.stderr)
        return None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name), header=None, names=COLS)
    df = df[["open_time", "open", "high", "low", "close", "volume"]]
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def load(tf: str = "15m", start: tuple[int, int] = (2020, 1),
         end: tuple[int, int] | None = None, sym: str = "BTCEUR") -> pd.DataFrame:
    """Retourne un DataFrame OHLCV indexé par date UTC."""
    if end is None:
        t = date.today()
        end = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    parts = [d for y, m in _months(start, end) if (d := _download_month(sym, tf, y, m)) is not None]
    if not parts:
        raise RuntimeError("Aucune donnée téléchargée")
    df = pd.concat(parts, ignore_index=True)
    ts = df["open_time"].astype("int64")
    # Binance est passé des millisecondes aux microsecondes en 2025.
    ts = ts.where(ts < 10**14, ts // 1000)
    df.index = pd.to_datetime(ts, unit="ms", utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Agrège des bougies (ex. '1h', '4h', '1D')."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna()


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    d = load(tf)
    print(f"{tf}: {len(d)} bougies, du {d.index[0]} au {d.index[-1]}")
