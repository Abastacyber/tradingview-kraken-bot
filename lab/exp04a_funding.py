"""
EXPÉRIENCE 04a — Le taux de financement (levier des traders) améliore-t-il le gardien ?

Indicateur : F7 = moyenne sur 7 jours du funding journalier des perpétuels (BTCUSDT / ETHUSDT).
Seuils calculés UNIQUEMENT sur la période d'étude 2020-2023 (quantiles 80 % et 90 %),
puis appliqués tels quels à la période de contrôle 2024-2026.

Hypothèses fixées AVANT de regarder les résultats :
  H1 veto    : ne pas entrer quand F7 > seuil (levier extrême = euphorie).
  H2 dosage  : investir 50 % au lieu de 100 % quand F7 > q80.
  H3 sortie  : sortir quand F7 > q90, et ne pas ré-entrer tant que F7 > q90.
Base : gardien « état » EMA rapide/lente + EMA200, sortie sur signal (EXP03), 16 réglages voisins.
Critère : la variante doit faire mieux que la base sur la période de CONTRÔLE, pour la majorité
des réglages, sur BTC ET ETH. Sinon : rejetée.
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
import data_funding  # noqa: E402
import engine as E  # noqa: E402
import strategies as S  # noqa: E402

FEES = {"0,80 %": E.Costs(0.0080, 0.0080, 0.0005), "0,40 %": E.Costs(0.0040, 0.0040, 0.0005)}
IS, OOS = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01")
GRID = list(itertools.product((10, 15, 20, 30), (40, 50, 75, 100)))
MARKETS = {"BTC/EUR": ("BTCEUR", "BTCUSDT"), "ETH/EUR": ("ETHEUR", "ETHUSDT")}


def variants(d, f7, q80, q90, fast, slow):
    up, dn = S.ema_state(d, fast, slow, 200)
    hi80, hi90 = f7 > q80, f7 > q90
    return {
        "base (gardien seul)": (up, dn, None),
        "H1 veto si levier > q80": (up & ~hi80, dn, None),
        "H1 veto si levier > q90": (up & ~hi90, dn, None),
        "H2 dosage 50 % si levier > q80": (up, dn, (~hi80).astype(float) * 0.5 + 0.5),
        "H3 sortie si levier > q90": (up & ~hi90, dn | hi90, None),
    }


def run(d, ent, xit, size, c, period):
    a, b = period
    r = E.run(d.loc[a:b], ent.loc[a:b], xit.loc[a:b], E.Exits(), c,
              size=size.loc[a:b] if size is not None else None)
    m = E.metrics(r)
    return m["total_%"], m["max_dd_%"], m["trades"]


def md(df):
    cols = list(df.columns)
    return ("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
            + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values))


def main():
    rows = []
    for mk, (spot, perp) in MARKETS.items():
        d = data.resample(data.load("1h", sym=spot), "1D")
        f7 = data_funding.load(perp).rolling(7).mean().reindex(d.index).ffill()
        q80, q90 = f7.loc[IS[0]:IS[1]].quantile([0.8, 0.9])
        for fl, c in FEES.items():
            res = {}
            for fast, slow in GRID:
                for name, (ent, xit, size) in variants(d, f7, q80, q90, fast, slow).items():
                    res.setdefault(name, []).append(
                        run(d, ent, xit, size, c, IS) + run(d, ent, xit, size, c, OOS))
            base = pd.DataFrame(res["base (gardien seul)"], columns=["is", "is_dd", "is_n", "oos", "oos_dd", "oos_n"])
            for name, vals in res.items():
                v = pd.DataFrame(vals, columns=base.columns)
                rows.append({
                    "marché": mk, "frais": fl, "variante": name,
                    "contrôle médian %": round(v.oos.median(), 1),
                    "perte max médiane %": round(v.oos_dd.median(), 1),
                    "% réglages > base (contrôle)": "—" if name.startswith("base") else round(100 * (v.oos > base.oos).mean()),
                    "% réglages > base (étude)": "—" if name.startswith("base") else round(100 * (v["is"] > base["is"]).mean()),
                    "trades contrôle (médian)": int(v.oos_n.median()),
                })
        rows.append({"marché": mk, "frais": "seuils", "variante": f"q80 = {q80*365*100:.0f} %/an · q90 = {q90*365*100:.0f} %/an",
                     "contrôle médian %": "", "perte max médiane %": "", "% réglages > base (contrôle)": "",
                     "% réglages > base (étude)": "", "trades contrôle (médian)": ""})
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    out = [f"# EXPÉRIENCE 04a — Taux de financement et gardien journalier ({date.today().isoformat()})", "",
           "Seuils de levier calculés sur 2020-2023 uniquement. Étude 2020-2023, contrôle 2024 → fin octobre 2026.",
           "16 réglages EMA voisins (rapide 10-30, lente 40-100) + filtre EMA200. Sortie sur signal.", "",
           md(pd.DataFrame(rows)), ""]
    path = os.path.join(res_dir, "EXP04a_funding.md")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(path)


if __name__ == "__main__":
    main()
