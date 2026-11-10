"""
EXPÉRIENCE 01 — Les frais, l'horizon de temps et les sorties.

Questions posées (en langage de trader) :
  Q1. Les sorties ORION actuelles (stop 3 %, trailing activé à +0,4 %, écart 0,2 %)
      peuvent-elles gagner de l'argent avec les frais Kraken réels ?
  Q2. À partir de quel horizon (15 min, 1 h, 4 h, 1 jour) des stratégies simples
      et connues deviennent-elles rentables NET de frais ?
  Q3. Combien gagne-t-on en passant d'ordres market (taker) à des ordres limit (maker) ?

Méthode anti-illusion :
  - période d'étude (IS) : 2020-01 → 2023-12 ; période de contrôle (OOS) : 2024-01 → fin.
  - AUCUNE optimisation : quelques réglages standards, tous affichés (gagnants et perdants).
  - Comparaison systématique avec l'achat-conservation (buy & hold).

Lancement : python lab/exp01_frais_et_horizon.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402

import data  # noqa: E402
import engine as E  # noqa: E402
import strategies as S  # noqa: E402

TAKER = E.Costs(0.0040, 0.0040, 0.0005)   # ordres market, palier Kraken de départ
MAKER = E.Costs(0.0025, 0.0025, 0.0)      # ordres limit (hypothèse : toujours exécutés)
ZERO = E.Costs(0.0, 0.0, 0.0)             # référence "sans frais" (brut)
# Grille 2026 rapportée par plusieurs sites (NON confirmée par Kraken) : taker 0,80 %
TAKER_2026 = E.Costs(0.0080, 0.0080, 0.0005)
PERIODS = {"IS 2020-2023": ("2020-01-01", "2023-12-31"),
           "OOS 2024-2026": ("2024-01-01", "2100-01-01")}

ORION_EXITS = E.Exits(stop_pct=0.03, trail_activate_pct=0.004, trail_gap_pct=0.002)
TREND_EXITS = E.Exits(stop_atr=3.0, trail_atr=4.0)


def run_case(df, name, tf, sig, exits, costs, label_costs):
    ent, ex = sig
    rows = []
    for pname, (a, b) in PERIODS.items():
        d = df.loc[a:b]
        r = E.run(d, ent.loc[a:b], ex.loc[a:b], exits, costs)
        m = E.metrics(r)
        rows.append({"stratégie": name, "tf": tf, "frais": label_costs, "période": pname, **m})
    return rows


def table(rows, cols):
    df = pd.DataFrame(rows)[cols]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values)


def main():
    raw = data.load("15m")
    frames = {"15m": raw, "1h": data.resample(raw, "1h"), "4h": data.resample(raw, "4h"),
              "1D": data.resample(raw, "1D")}
    out = []
    cols = ["stratégie", "tf", "frais", "période", "trades", "trades_par_mois", "win_rate_%",
            "esperance_brut_%", "esperance_net_%", "profit_factor", "total_%", "max_dd_%", "exposition_%"]

    # ---------- Q1 : sorties ORION actuelles ----------
    q1 = []
    df = frames["15m"]
    sig = S.ema_cross(df, 20, 50)
    for lab, c in (("sans frais", ZERO), ("taker 0,40 %", TAKER), ("maker 0,25 %", MAKER)):
        q1 += run_case(df, "EMA20/50 + sorties ORION actuelles", "15m", sig, ORION_EXITS, c, lab)
        q1 += run_case(df, "EMA20/50 + sorties larges (ATR)", "15m", sig, TREND_EXITS, c, lab)

    # ---------- Q2 / Q3 : familles connues sur plusieurs horizons ----------
    q2 = []
    for tf, d in frames.items():
        cases = [
            ("EMA 20/50 (filtre EMA200)", S.ema_cross(d, 20, 50, trend=200), TREND_EXITS),
            ("Donchian 20/10", S.donchian(d, 20, 10), E.Exits(stop_atr=3.0)),
            ("Donchian 55/20", S.donchian(d, 55, 20), E.Exits(stop_atr=3.0)),
            ("RSI2 pullback (Connors)", S.rsi2_pullback(d), E.Exits(stop_atr=3.0)),
        ]
        for name, sig, ex in cases:
            scen = [("taker 0,40 %", TAKER), ("maker 0,25 %", MAKER)]
            if tf in ("4h", "1D"):
                scen.append(("taker 0,80 % (2026?)", TAKER_2026))
            for lab, c in scen:
                q2 += run_case(d, name, tf, sig, ex, c, lab)

    bh = {p: E.buy_and_hold(frames["1D"].loc[a:b], TAKER) for p, (a, b) in PERIODS.items()}

    # ---------- Rapport ----------
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    pd.DataFrame(q1 + q2).to_csv(os.path.join(res_dir, "exp01_raw.csv"), index=False)
    lines = [
        f"# EXPÉRIENCE 01 — Frais, horizon et sorties ({date.today().isoformat()})",
        "",
        f"Données : BTC/EUR, {raw.index[0]:%Y-%m-%d} → {raw.index[-1]:%Y-%m-%d}, bougies 15 min agrégées.",
        "Coûts : taker = 0,40 % par ordre + 0,05 % de glissement ; maker = 0,25 % sans glissement ; scénario 2026 non confirmé : taker 0,80 %.",
        "Capital entièrement investi à chaque trade, une position à la fois, long uniquement.",
        "",
        "## Référence : acheter et conserver (buy & hold)",
        "",
        "| période | total_% | cagr_% | max_dd_% |\n|---|---|---|---|",
        *[f"| {p} | {v['total_%']} | {v['cagr_%']} | {v['max_dd_%']} |" for p, v in bh.items()],
        "",
        "## Q1 — Les sorties ORION actuelles résistent-elles aux frais ?",
        "",
        table(q1, cols),
        "",
        "## Q2/Q3 — Stratégies connues, par horizon et par type d'ordre",
        "",
        table(q2, cols),
        "",
    ]
    path = os.path.join(res_dir, "EXP01_frais_horizon.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print(path)


if __name__ == "__main__":
    main()
