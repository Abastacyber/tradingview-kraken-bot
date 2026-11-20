"""
EXPÉRIENCE 04b — (A) Le gardien marche-t-il sur d'autres marchés ? (B) Les données
« institutionnelles » et de sentiment l'améliorent-elles ?

A. MARCHÉS : 7 cryptos en EUR (BTC, ETH, SOL, XRP, ADA, BNB, DOGE), une devise (EUR/USD via le jeton
   EURUSDT) et l'or (via PAXG/USDT, jeton adossé à l'or physique). Même règle, 16 réglages voisins.
   Les 200 premiers jours de chaque marché servent seulement à chauffer l'EMA200 (pas d'achat).

B. FILTRES (BTC et ETH), hypothèses fixées AVANT de regarder :
   F1  Peur & Avidité : ne pas acheter si l'indice ≥ 75 (« avidité extrême », seuil standard).
   F2  Peur & Avidité : investir 50 % si l'indice ≥ 75.
   C1  COT : n'acheter que si les gestionnaires d'actifs n'ont pas réduit leur position nette sur 4 semaines.
   C2  COT : investir 50 % s'ils l'ont réduite.
   V1  Volatilité implicite (DVOL) : ne pas acheter si DVOL > quantile 80 % de la période d'étude.
   V2  DVOL : investir 50 % si DVOL > ce seuil.
   Critère d'acceptation : mieux que le gardien seul sur l'étude ET le contrôle, pour la majorité des réglages,
   sur BTC ET ETH. Frais 0,80 % (hypothèse Kraken 2026) + glissement 0,05 %.
"""
from __future__ import annotations

import itertools
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402

import data  # noqa: E402
import data_alt as ALT  # noqa: E402
import engine as E  # noqa: E402
import strategies as S  # noqa: E402

C80 = E.Costs(0.0080, 0.0080, 0.0005)
C40 = E.Costs(0.0040, 0.0040, 0.0005)
IS, OOS = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01")
GRID = list(itertools.product((10, 15, 20, 30), (40, 50, 75, 100)))
MARKETS = {"BTC/EUR": "BTCEUR", "ETH/EUR": "ETHEUR", "SOL/EUR": "SOLEUR", "XRP/EUR": "XRPEUR",
           "ADA/EUR": "ADAEUR", "BNB/EUR": "BNBEUR", "DOGE/EUR": "DOGEEUR",
           "EUR/USD (devise)": "EURUSDT", "Or (PAXG)": "PAXGUSDT"}


def md(df):
    cols = list(df.columns)
    return ("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
            + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values))


def daily(sym):
    return data.load("1d", start=(2020, 1), sym=sym)


def signals(d, fast, slow):
    up, dn = S.ema_state(d, fast, slow, 200)
    up = up.copy(); up.iloc[:200] = False  # chauffe de l'EMA200
    return up, dn


def tot(d, ent, xit, c, period, size=None):
    a, b = period
    dd = d.loc[a:b]
    if len(dd) < 30:
        return float("nan"), float("nan")
    r = E.run(dd, ent.loc[a:b], xit.loc[a:b], E.Exits(), c, size=size.loc[a:b] if size is not None else None)
    m = E.metrics(r)
    return m["total_%"], m["max_dd_%"]


def part_a():
    rows = []
    for name, sym in MARKETS.items():
        d = daily(sym)
        start_trading = d.index[min(200, len(d) - 1)]
        res = {"is80": [], "oos80": [], "dd80": [], "oos40": []}
        for f, s in GRID:
            up, dn = signals(d, f, s)
            i80, _ = tot(d, up, dn, C80, IS)
            o80, dd80 = tot(d, up, dn, C80, OOS)
            o40, _ = tot(d, up, dn, C40, OOS)
            res["is80"].append(i80); res["oos80"].append(o80); res["dd80"].append(dd80); res["oos40"].append(o40)
        r = pd.DataFrame(res)
        bh = E.buy_and_hold(d.loc[OOS[0]:], C80)
        rows.append({
            "marché": name, "achats possibles dès": f"{start_trading:%m/%Y}",
            "% réglages gagnants (étude ET contrôle)": round(100 * ((r.is80 > 0) & (r.oos80 > 0)).mean()),
            "gardien contrôle médian % (frais 0,80)": round(r.oos80.median(), 1),
            "gardien contrôle médian % (frais 0,40)": round(r.oos40.median(), 1),
            "gardien perte max médiane %": round(r.dd80.median(), 1),
            "acheter-garder contrôle %": bh["total_%"], "acheter-garder perte max %": bh["max_dd_%"],
        })
    return pd.DataFrame(rows)


def part_b():
    fg = ALT.fear_greed()
    rows = []
    for mk, sym, asset in (("BTC/EUR", "BTCEUR", "BTC"), ("ETH/EUR", "ETHEUR", "ETH")):
        d = daily(sym)
        idx = d.index
        fgd = fg.reindex(idx).ffill()
        cot = ALT.cot_asset_managers(asset).reindex(idx, method="ffill")
        cot_chg = cot - cot.shift(28)
        dv = ALT.dvol(asset).reindex(idx).ffill()
        dv_q80 = dv.loc[IS[0]:IS[1]].quantile(0.8)
        greed = (fgd >= 75).fillna(False)
        cot_down = (cot_chg < 0).fillna(False)
        vol_hi = (dv > dv_q80).fillna(False)
        half = lambda m: (~m).astype(float) * 0.5 + 0.5  # noqa: E731
        res = {}
        for f, s in GRID:
            up, dn = signals(d, f, s)
            var = {
                "gardien seul": (up, None),
                "F1 veto avidité ≥ 75": (up & ~greed, None),
                "F2 dosage 50 % si avidité ≥ 75": (up, half(greed)),
                "C1 veto si fonds réduisent (4 sem.)": (up & ~cot_down, None),
                "C2 dosage 50 % si fonds réduisent": (up, half(cot_down)),
                "V1 veto si volatilité implicite haute": (up & ~vol_hi, None),
                "V2 dosage 50 % si volatilité haute": (up, half(vol_hi)),
            }
            for name, (ent, size) in var.items():
                res.setdefault(name, []).append(tot(d, ent, dn, C80, IS, size) + tot(d, ent, dn, C80, OOS, size))
        base = pd.DataFrame(res["gardien seul"], columns=["is", "is_dd", "oos", "oos_dd"])
        for name, vals in res.items():
            v = pd.DataFrame(vals, columns=base.columns)
            first = name == "gardien seul"
            rows.append({
                "marché": mk, "variante": name,
                "étude médian %": round(v["is"].median(), 1), "contrôle médian %": round(v.oos.median(), 1),
                "perte max contrôle médiane %": round(v.oos_dd.median(), 1),
                "% réglages > gardien (étude)": "—" if first else round(100 * (v["is"] > base["is"]).mean()),
                "% réglages > gardien (contrôle)": "—" if first else round(100 * (v.oos > base.oos).mean()),
                "% réglages perte max réduite (contrôle)": "—" if first else round(100 * (v.oos_dd > base.oos_dd).mean()),
            })
    return pd.DataFrame(rows)


def main():
    a, b = part_a(), part_b()
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    out = [f"# EXPÉRIENCE 04b — Autres marchés et données institutionnelles ({date.today().isoformat()})", "",
           "Règle gardien (EMA rapide > lente ET prix > EMA200 ; vente quand rapide < lente), 16 réglages voisins,",
           "bougies journalières. Étude 2020-2023 (ou depuis la cotation), contrôle 2024 → fin octobre 2026.", "",
           "## A. Le gardien sur d'autres marchés", "", md(a), "",
           "## B. Filtres institutionnels et de sentiment (frais 0,80 %)", "", md(b), ""]
    path = os.path.join(res_dir, "EXP04b_marches_institutionnels.md")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(path)


if __name__ == "__main__":
    main()
