"""
ORION LAB — moteur de backtest (long uniquement, spot, une position à la fois).

Règles de simulation (volontairement prudentes) :
- Un signal est calculé à la CLÔTURE d'une bougie ; l'ordre est exécuté à
  l'OUVERTURE de la bougie suivante (pas de "regard vers le futur").
- Frais appliqués à l'entrée ET à la sortie (par défaut : taker Kraken 0,40 %).
- Glissement (slippage) appliqué à chaque ordre market.
- Stop : vérifié avec le stop connu AVANT la bougie. Si la bougie ouvre déjà
  sous le stop (gap), la sortie se fait au prix d'ouverture, pas au stop.
- Le trailing est remonté avec le plus haut de la bougie, mais ne s'applique
  qu'à partir de la bougie suivante (on ne sait pas, dans une bougie OHLC,
  si le plus haut est venu avant ou après le plus bas).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Costs:
    fee_entry: float = 0.0040   # 0,40 % (taker Kraken, palier de départ)
    fee_exit: float = 0.0040
    slippage: float = 0.0005    # 0,05 % par ordre market

    @property
    def round_trip(self) -> float:
        return self.fee_entry + self.fee_exit + 2 * self.slippage


@dataclass
class Exits:
    """Modules de sortie. Tous optionnels, combinables."""
    stop_pct: Optional[float] = None        # stop initial fixe en % (ex. 0.03)
    stop_atr: Optional[float] = None        # stop initial = k x ATR
    trail_activate_pct: Optional[float] = None  # trailing ORION actuel : activation
    trail_gap_pct: Optional[float] = None       # trailing ORION actuel : écart
    trail_atr: Optional[float] = None       # trailing "chandelier" = plus haut - k x ATR
    breakeven_at_r: Optional[float] = None  # stop remonté au break-even NET à +k R
    max_bars: Optional[int] = None          # sortie au bout de N bougies
    use_signal_exit: bool = True            # sortie sur signal de la stratégie


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float   # prix payé (slippage inclus)
    exit_price: float    # prix reçu (slippage inclus)
    raw_entry: float
    raw_exit: float
    bars: int
    reason: str
    gross: float = 0.0   # rendement brut (sans frais ni slippage)
    net: float = 0.0     # rendement net (frais + slippage)


@dataclass
class Result:
    trades: list
    equity: pd.Series      # capital (base 1.0) marqué au marché, bougie par bougie
    bars_in_market: int
    total_bars: int
    costs: Costs
    meta: dict = field(default_factory=dict)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def run(df: pd.DataFrame, entries: pd.Series, exits_sig: Optional[pd.Series] = None,
        exits: Exits = Exits(), costs: Costs = Costs(), atr_n: int = 14) -> Result:
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    idx = df.index
    a = atr(df, atr_n).to_numpy()
    ent = entries.reindex(idx).fillna(False).to_numpy(dtype=bool)
    exs = (exits_sig.reindex(idx).fillna(False).to_numpy(dtype=bool)
           if exits_sig is not None else np.zeros(len(df), dtype=bool))

    slip, fe, fx = costs.slippage, costs.fee_entry, costs.fee_exit
    trades: list[Trade] = []
    equity = np.empty(len(df)); cash = 1.0
    in_pos = False; pending_entry = False; pending_exit = False
    entry_i = 0; raw_entry = paid = stop = maxp = 0.0; risk = 0.0
    trail_on = False; be_done = False; qty = 0.0; init_stop = 0.0
    in_market = 0

    def close_trade(i, raw_px, reason):
        nonlocal in_pos, cash, qty
        px = raw_px * (1 - slip)
        proceeds = qty * px * (1 - fx)
        cash = proceeds
        t = Trade(idx[entry_i], idx[i], paid, px, raw_entry, raw_px, i - entry_i, reason)
        t.gross = raw_px / raw_entry - 1
        t.net = (px * (1 - fx)) / (paid * (1 + fe)) - 1
        trades.append(t)
        in_pos = False; qty = 0.0

    for i in range(len(df)):
        # 1) ordres programmés à l'ouverture
        if in_pos and pending_exit:
            close_trade(i, o[i], "signal"); pending_exit = False
        if not in_pos and pending_entry:
            pending_entry = False
            raw_entry = o[i]; paid = raw_entry * (1 + slip)
            qty = cash / (paid * (1 + fe))
            in_pos = True; entry_i = i; maxp = raw_entry; trail_on = False; be_done = False
            stops = []
            if exits.stop_pct: stops.append(raw_entry * (1 - exits.stop_pct))
            if exits.stop_atr and np.isfinite(a[i - 1] if i else np.nan):
                stops.append(raw_entry - exits.stop_atr * a[i - 1])
            stop = max(stops) if stops else 0.0
            init_stop = stop
            risk = raw_entry - stop if stop > 0 else 0.0

        # 2) gestion de la position pendant la bougie
        if in_pos:
            if stop > 0 and o[i] <= stop:
                close_trade(i, o[i], "stop_gap")
            elif stop > 0 and l[i] <= stop:
                close_trade(i, stop, "trail" if stop > init_stop + 1e-9 else "stop")
            else:
                maxp = max(maxp, h[i])
                if exits.trail_activate_pct is not None and exits.trail_gap_pct is not None:
                    if not trail_on and maxp >= raw_entry * (1 + exits.trail_activate_pct):
                        trail_on = True
                    if trail_on:
                        stop = max(stop, maxp * (1 - exits.trail_gap_pct))
                if exits.trail_atr and np.isfinite(a[i]):
                    stop = max(stop, maxp - exits.trail_atr * a[i])
                if exits.breakeven_at_r and risk > 0 and not be_done and maxp >= raw_entry + exits.breakeven_at_r * risk:
                    net_be = paid * (1 + fe) / ((1 - fx) * (1 - slip))
                    stop = max(stop, net_be); be_done = True
                if exits.max_bars and i - entry_i >= exits.max_bars:
                    pending_exit = True
                if exits.use_signal_exit and exs[i]:
                    pending_exit = True

        # 3) nouveau signal d'entrée (exécuté à la prochaine ouverture)
        if not in_pos and ent[i]:
            pending_entry = True

        if in_pos:
            in_market += 1
            equity[i] = qty * c[i] * (1 - fx)
        else:
            equity[i] = cash

    if in_pos:  # clôture de fin de période
        close_trade(len(df) - 1, c[-1], "end")
        equity[-1] = cash

    return Result(trades, pd.Series(equity, index=idx), in_market, len(df), costs)


def metrics(r: Result) -> dict:
    t = r.trades
    eq = r.equity
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    dd = (eq / eq.cummax() - 1).min()
    out = {
        "trades": len(t),
        "trades_par_mois": round(len(t) / (years * 12), 1),
        "total_%": round((eq.iloc[-1] - 1) * 100, 1),
        "cagr_%": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1) if eq.iloc[-1] > 0 else -100.0,
        "max_dd_%": round(dd * 100, 1),
        "exposition_%": round(100 * r.bars_in_market / max(r.total_bars, 1), 1),
    }
    if t:
        net = np.array([x.net for x in t]); gross = np.array([x.gross for x in t])
        wins, losses = net[net > 0], net[net <= 0]
        out.update({
            "win_rate_%": round(100 * len(wins) / len(net), 1),
            "gain_moy_%": round(100 * wins.mean(), 2) if len(wins) else 0.0,
            "perte_moy_%": round(100 * losses.mean(), 2) if len(losses) else 0.0,
            "profit_factor": round(wins.sum() / -losses.sum(), 2) if losses.sum() < 0 else float("inf"),
            "esperance_net_%": round(100 * net.mean(), 3),
            "esperance_brut_%": round(100 * gross.mean(), 3),
            "duree_moy_bougies": round(float(np.mean([x.bars for x in t])), 1),
        })
    return out


def buy_and_hold(df: pd.DataFrame, costs: Costs = Costs()) -> dict:
    ret = (df["close"].iloc[-1] * (1 - costs.slippage) * (1 - costs.fee_exit)) / \
          (df["open"].iloc[0] * (1 + costs.slippage) * (1 + costs.fee_entry)) - 1
    eq = df["close"] / df["close"].iloc[0]
    years = (df.index[-1] - df.index[0]).days / 365.25
    return {"total_%": round(ret * 100, 1),
            "cagr_%": round(((1 + ret) ** (1 / years) - 1) * 100, 1),
            "max_dd_%": round((eq / eq.cummax() - 1).min() * 100, 1)}
