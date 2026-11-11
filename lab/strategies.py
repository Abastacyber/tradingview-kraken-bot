"""
ORION LAB — familles de stratégies testées.

Chaque fonction reçoit un DataFrame OHLCV et renvoie (entries, exits) :
deux séries booléennes évaluées à la CLÔTURE de chaque bougie.
"""
from __future__ import annotations

import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-12))


def ema_cross(df, fast=20, slow=50, trend=None):
    """Suivi de tendance : la moyenne rapide croise au-dessus de la lente."""
    f, s = ema(df.close, fast), ema(df.close, slow)
    up = (f > s) & (f.shift(1) <= s.shift(1))
    dn = (f < s) & (f.shift(1) >= s.shift(1))
    if trend:
        up &= df.close > ema(df.close, trend)
    return up, dn


def donchian(df, n_entry=20, n_exit=10, trend=None):
    """Cassure (breakout) : clôture au-dessus du plus haut des N dernières bougies."""
    hh = df.high.rolling(n_entry).max().shift(1)
    ll = df.low.rolling(n_exit).min().shift(1)
    up = df.close > hh
    dn = df.close < ll
    if trend:
        up &= df.close > ema(df.close, trend)
    return up, dn


def rsi2_pullback(df, rsi_n=2, buy_below=10, trend=200, exit_ema=5):
    """Retour à la moyenne dans une tendance haussière (héritage Connors RSI d'AEGIS)."""
    r = rsi(df.close, rsi_n)
    up = (r < buy_below) & (df.close > ema(df.close, trend))
    dn = df.close > ema(df.close, exit_ema)
    return up, dn


def ema_state(df, fast=20, slow=50, trend=200):
    """Variante « état » : on est acheteur TANT QUE la moyenne rapide est au-dessus de la lente
    et le prix au-dessus de l'EMA de tendance (pas seulement au moment du croisement).
    Utile pour démarrer le bot en cours de tendance."""
    f, s = ema(df.close, fast), ema(df.close, slow)
    up = (f > s)
    if trend:
        up &= df.close > ema(df.close, trend)
    dn = f < s
    return up, dn
