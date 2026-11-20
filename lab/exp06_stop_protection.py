"""
EXPÉRIENCE 06 — Calibrer le STOP DE PROTECTION du gardien (avant l'étape 5).

Constat d'EXP03 : avec l'entrée « état », un stop à 3 ATR DÉGRADE le gardien (BTC contrôle +62 % au lieu de +68 %,
pire réglage +26 % au lieu de +49 %) : le stop vend sur une secousse, puis le gardien rachète le lendemain
(les conditions d'achat sont toujours vraies) → allers-retours et frais.

Question : existe-t-il un stop « assurance anti-krach / anti-panne » qui ne coûte presque rien en temps normal
et limite la pire perte quand le serveur ne peut pas vendre ?

Variantes testées (16 réglages EMA voisins, entrée « état », filtre EMA200) :
  - stop INITIAL (fixé à l'achat) : 3, 5, 8 ATR ; -15 %, -20 % sous le prix d'achat ;
  - stop SUIVEUR large (remonte avec le plus haut) : 8, 12 ATR ; -20 %, -25 % sous le plus haut ;
  - chacun avec et sans VERROU « pas de rachat après un stop tant que EMA rapide n'est pas repassée sous la lente ».

Coûts (hypothèses prudentes, écrites avant les tests) :
  - achats et ventes sur signal : ordres limit (maker) 0,40 % + glissement 0,05 % ;
  - sortie par stop : ordre stop Kraken = taker 0,80 % + glissement 0,50 % (marché qui chute).

Scénarios :
  - normal ;
  - PANNE : le serveur rate le signal de vente pendant 7 jours (la vente sur signal arrive 7 jours en retard) ;
    seul le stop posé chez Kraken peut alors protéger.

CRITÈRE DE DÉCISION (fixé AVANT de lancer les calculs) — sur BTC/EUR :
  Un stop est ACCEPTABLE si
    (a) ≥ 90 % des réglages restent gagnants sur les 2 périodes (étude 2020-2023 et contrôle 2024-2026) ;
    (b) le rendement médian baisse d'au plus 3 points par rapport à « aucun stop », en étude ET en contrôle ;
    (c) il se déclenche au plus 1 fois par an (médiane, 2020-2026).
  Parmi les acceptables, on RETIENT celui qui réduit le plus la pire perte d'un trade dans le scénario PANNE.
  Confirmation : ETH/EUR ne doit pas contredire le choix (pas de dégradation > 3 points en contrôle).
  Si aucun n'est acceptable : le stop sera limité à une assurance anti-panne la plus large possible.

Lancement : python lab/exp06_stop_protection.py
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

COSTS = E.Costs(0.0040, 0.0040, 0.0005, fee_stop=0.0080, slip_stop=0.0050)
IS, OOS, ALL = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01"), ("2020-01-01", "2100-01-01")
GRID = list(itertools.product((10, 15, 20, 30), (40, 50, 75, 100)))
PANNE_JOURS = 7

STOPS = {
    "aucun stop": {},
    "initial 3 ATR": {"stop_atr": 3.0},
    "initial 5 ATR": {"stop_atr": 5.0},
    "initial 8 ATR": {"stop_atr": 8.0},
    "initial -15 %": {"stop_pct": 0.15},
    "initial -20 %": {"stop_pct": 0.20},
    "suiveur 8 ATR": {"trail_atr": 8.0},
    "suiveur 12 ATR": {"trail_atr": 12.0},
    "suiveur -20 %": {"trail_activate_pct": 0.0, "trail_gap_pct": 0.20},
    "suiveur -25 %": {"trail_activate_pct": 0.0, "trail_gap_pct": 0.25},
}


def variants():
    for name, kw in STOPS.items():
        yield name, False, E.Exits(**kw)
        if kw:
            yield name, True, E.Exits(**kw, no_reentry_after_stop=True)


def md_table(df):
    cols = list(df.columns)
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values)


def run(d, ent, xit, ex, period):
    a, b = period
    return E.run(d.loc[a:b], ent.loc[a:b], xit.loc[a:b], ex, COSTS)


def study(d):
    rows = []
    years = (d.index[-1] - d.index[0]).days / 365.25
    for name, lock, ex in variants():
        res = []
        for f, s in GRID:
            ent, xit = S.ema_state(d, f, s, 200)
            mi, mo = E.metrics(run(d, ent, xit, ex, IS)), E.metrics(run(d, ent, xit, ex, OOS))
            full = run(d, ent, xit, ex, ALL)
            n_stops = sum(t.reason in ("stop", "stop_gap", "trail") for t in full.trades)
            panne = run(d, ent, xit.shift(PANNE_JOURS, fill_value=False), ex, ALL)
            pm = E.metrics(panne)
            res.append((mi["total_%"], mo["total_%"], mo["max_dd_%"],
                        min(t.net for t in full.trades) * 100, n_stops / years,
                        min(t.net for t in panne.trades) * 100, pm["max_dd_%"], pm["total_%"]))
        df = pd.DataFrame(res, columns=["is", "oos", "dd", "worst", "stops_an", "p_worst", "p_dd", "p_tot"])
        rows.append({
            "stop": name, "verrou": "oui" if lock else "non",
            "% gagnants 2 périodes": round(100 * ((df["is"] > 0) & (df.oos > 0)).mean()),
            "étude médian %": round(df["is"].median(), 1),
            "contrôle médian %": round(df.oos.median(), 1),
            "contrôle pire %": round(df.oos.min(), 1),
            "perte max contrôle %": round(df.dd.median(), 1),
            "pire trade %": round(df.worst.median(), 1),
            "stops / an": round(df.stops_an.median(), 2),
            "PANNE pire trade %": round(df.p_worst.median(), 1),
            "PANNE perte max %": round(df.p_dd.median(), 1),
            "PANNE total 2020-26 %": round(df.p_tot.median(), 1),
        })
    return pd.DataFrame(rows)


def verdict(btc, eth):
    base = btc[btc.stop == "aucun stop"].iloc[0]
    ok = btc[(btc.stop != "aucun stop")
             & (btc["% gagnants 2 périodes"] >= 90)
             & (btc["étude médian %"] >= base["étude médian %"] - 3)
             & (btc["contrôle médian %"] >= base["contrôle médian %"] - 3)
             & (btc["stops / an"] <= 1.0)]
    lines = [f"Référence sans stop (BTC) : étude {base['étude médian %']} %, contrôle {base['contrôle médian %']} %, "
             f"PANNE pire trade {base['PANNE pire trade %']} %.", ""]
    if ok.empty:
        lines.append("**Aucun stop ne remplit le critère (a)+(b)+(c).**")
        return "\n".join(lines), None
    lines.append("Stops ACCEPTABLES (critères a, b, c remplis) :")
    for _, r in ok.iterrows():
        lines.append(f"- {r.stop} (verrou {r.verrou}) : contrôle {r['contrôle médian %']} %, étude {r['étude médian %']} %, "
                     f"{r['stops / an']} stop/an, PANNE pire trade {r['PANNE pire trade %']} %")
    best = ok.sort_values(["PANNE pire trade %", "contrôle médian %"], ascending=[False, False]).iloc[0]
    e = eth[(eth.stop == best.stop) & (eth.verrou == best.verrou)].iloc[0]
    e0 = eth[eth.stop == "aucun stop"].iloc[0]
    eth_ok = e["contrôle médian %"] >= e0["contrôle médian %"] - 3
    lines += ["", f"**Retenu selon le critère : {best.stop}, verrou {best.verrou}.**",
              f"Confirmation ETH : contrôle {e['contrôle médian %']} % contre {e0['contrôle médian %']} % sans stop → "
              + ("pas de contradiction ✅" if eth_ok else "DÉGRADATION > 3 points ❌ (choix à rediscuter)")]
    return "\n".join(lines), best


def sensitivity(d):
    """Annexe : le stop suiveur en ATR est-il stable autour du réglage retenu (10 / 12 / 14 / 16 ATR) ?"""
    global STOPS
    saved = STOPS
    STOPS = {"aucun stop": {}, **{f"suiveur {k} ATR": {"trail_atr": float(k)} for k in (10, 12, 14, 16)}}
    try:
        t = study(d)
    finally:
        STOPS = saved
    t = t[t.verrou == "non"]
    return t[["stop", "% gagnants 2 périodes", "étude médian %", "contrôle médian %", "contrôle pire %",
              "stops / an", "PANNE pire trade %", "PANNE perte max %"]]


def main():
    out = {}
    for sym, label in (("BTCEUR", "BTC/EUR"), ("ETHEUR", "ETH/EUR")):
        d = data.resample(data.load("1h", sym=sym), "1D")
        out[label] = (d, study(d))
    btc, eth = out["BTC/EUR"][1], out["ETH/EUR"][1]
    text, _ = verdict(btc, eth)
    d = out["BTC/EUR"][0]
    lines = [f"# EXPÉRIENCE 06 — Stop de protection du gardien ({date.today().isoformat()})", "",
             f"Données : bougies journalières {d.index[0]:%d/%m/%Y} → {d.index[-1]:%d/%m/%Y}. "
             "Étude 2020-2023, contrôle 2024 → fin. Médianes sur 16 réglages EMA voisins (entrée « état », filtre EMA200).",
             "Coûts : ordres sur signal limit 0,40 % + 0,05 % ; sortie par stop taker 0,80 % + 0,50 % de glissement.",
             f"PANNE : la vente sur signal arrive {PANNE_JOURS} jours en retard (serveur arrêté) ; seul le stop chez Kraken protège.",
             "", "## Verdict (critère fixé avant les calculs)", "", text, "",
             "## BTC/EUR", "", md_table(btc), "", "## ETH/EUR (confirmation)", "", md_table(eth), "",
             "## Annexe — sensibilité du stop suiveur ATR", ""]
    for label, (dd, _) in out.items():
        a = E.atr(dd) / dd.close * 100
        lines += [f"### {label} — ATR14 journalier : médiane {a.median():.1f} % du prix (min {a.min():.1f} %, max {a.max():.1f} %)",
                  "", md_table(sensitivity(dd)), ""]
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    path = os.path.join(res_dir, "EXP06_stop_protection.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print(path)


if __name__ == "__main__":
    main()
