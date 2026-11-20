"""
EXPÉRIENCE 05 — Diversifier le gardien : BTC seul, ou BTC + ETH (+ BNB) ?

Chaque marché a sa propre enveloppe et son propre gardien (même règle : EMA rapide > lente ET prix > EMA200,
vente quand rapide < lente). Deux façons de gérer les enveloppes :
  - « sans rééquilibrage » : chaque enveloppe vit sa vie ;
  - « rééquilibrage annuel » : chaque 1er janvier, on remet les enveloppes à parts égales.
Hypothèse fixée avant : la diversification doit améliorer le rapport rendement / perte maximale
(rendement total ÷ |perte max|) sur l'étude ET le contrôle, pour la majorité des 16 réglages.

Frais : ordres limit (maker) 0,40 % sans glissement — c'est ce que fera le gardien — et, par prudence,
ordres market (taker) 0,80 % + 0,05 % de glissement.
"""
from __future__ import annotations

import itertools
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import data  # noqa: E402
import engine as E  # noqa: E402
import strategies as S  # noqa: E402

COSTS = {"limit 0,40 %": E.Costs(0.0040, 0.0040, 0.0), "market 0,80 %": E.Costs(0.0080, 0.0080, 0.0005)}
IS, OOS = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01")
GRID = list(itertools.product((10, 15, 20, 30), (40, 50, 75, 100)))
SYMS = {"BTC": "BTCEUR", "ETH": "ETHEUR", "BNB": "BNBEUR"}
BASKETS = {"BTC seul": ["BTC"], "BTC + ETH": ["BTC", "ETH"], "BTC + ETH + BNB": ["BTC", "ETH", "BNB"]}


def md(df):
    cols = list(df.columns)
    return ("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
            + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values))


def gardien_equity(d, fast, slow, costs, period):
    up, dn = S.ema_state(d, fast, slow, 200)
    up = up.copy(); up.iloc[:200] = False
    a, b = period
    return E.run(d.loc[a:b], up.loc[a:b], dn.loc[a:b], E.Exits(), costs).equity


def combine(curves: dict, rebalance: bool) -> pd.Series:
    df = pd.DataFrame(curves).ffill().dropna()
    df = df / df.iloc[0]
    n = df.shape[1]
    if not rebalance:
        return df.mean(axis=1)
    out, start_val = [], 1.0
    for _, seg in df.groupby(df.index.year):
        seg = seg / seg.iloc[0]
        port = seg.mean(axis=1) * start_val
        out.append(port)
        start_val = port.iloc[-1]
    return pd.concat(out)


def stats(eq: pd.Series):
    tot = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
    dd = (eq / eq.cummax() - 1).min() * 100
    return tot, dd, tot / abs(dd) if dd < 0 else np.inf


def main():
    daily = {k: data.load("1d", start=(2020, 1), sym=v) for k, v in SYMS.items()}
    common_start = max(d.index[0] for d in daily.values())
    daily = {k: d.loc[common_start:] for k, d in daily.items()}
    rows, per_cfg = [], {}
    for (cl, c), (pname, period) in itertools.product(COSTS.items(), (("étude", IS), ("contrôle", OOS))):
        for f, s in GRID:
            eqs = {k: gardien_equity(d, f, s, c, period) for k, d in daily.items()}
            for bname, members in BASKETS.items():
                for reb in (False, True):
                    if len(members) == 1 and reb:
                        continue
                    label = bname + ("" if len(members) == 1 else (" (rééquilibrage annuel)" if reb else " (sans rééquilibrage)"))
                    tot, dd, ratio = stats(combine({k: eqs[k] for k in members}, reb))
                    per_cfg.setdefault((cl, pname, label), []).append((tot, dd, ratio))
    base_label = "BTC seul"
    for (cl, pname, label), vals in per_cfg.items():
        v = np.array(vals); b = np.array(per_cfg[(cl, pname, base_label)])
        rows.append({
            "frais": cl, "période": pname, "portefeuille": label,
            "rendement médian %": round(float(np.median(v[:, 0])), 1),
            "perte max médiane %": round(float(np.median(v[:, 1])), 1),
            "rendement / perte max (médian)": round(float(np.median(v[:, 2])), 2),
            "% réglages : meilleur ratio que BTC seul": "—" if label == base_label else int(round(100 * np.mean(v[:, 2] > b[:, 2]))),
        })
    # corrélation des rendements journaliers (marché, pas stratégie)
    rets = pd.DataFrame({k: d.close.pct_change() for k, d in daily.items()}).dropna()
    corr = rets.corr().round(2)
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    out = [f"# EXPÉRIENCE 05 — Diversification du gardien ({date.today().isoformat()})", "",
           f"Période commune : depuis le {common_start:%d/%m/%Y} (cotation de la plus récente des trois paires) ;"
           " les 200 premiers jours servent à chauffer l'EMA200.", "16 réglages EMA voisins, enveloppes à parts égales.", "",
           md(pd.DataFrame(rows).sort_values(["frais", "période", "portefeuille"])), "",
           "## Corrélation des variations journalières (1 = bougent toujours ensemble)", "",
           md(corr.reset_index().rename(columns={"index": "marché"})), ""]
    path = os.path.join(res_dir, "EXP05_diversification.md")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(path)


if __name__ == "__main__":
    main()
