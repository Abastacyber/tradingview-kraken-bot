"""
EXPÉRIENCE 02 — Robustesse du suivi de tendance en 4 h et en journalier.

EXP01 a montré des résultats positifs nets de frais en 4 h / 1 jour, mais avec
peu de trades. Avant d'y croire, trois tests de « chance ou solidité » :

  A. VOISINAGE : si on change un peu les réglages (moyennes, périodes), le
     résultat reste-t-il positif ? Une vraie stratégie forme un « plateau »,
     un coup de chance forme un « pic » isolé.
  B. ANNÉE PAR ANNÉE : la stratégie gagne-t-elle régulièrement, ou une seule
     année exceptionnelle fait-elle tout le résultat ?
  C. TIRAGES ALÉATOIRES (Monte-Carlo) : en remélangeant l'ordre des trades
     réels, quelle est la probabilité de perdre sur 2 ans, et quelle perte
     maximale faut-il être prêt à encaisser ?

Frais : taker 0,40 % (palier historique) et taker 0,80 % (grille 2026 rapportée, non confirmée).
Lancement : python lab/exp02_robustesse.py
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

FEES = {"0,40 %": E.Costs(0.0040, 0.0040, 0.0005), "0,80 %": E.Costs(0.0080, 0.0080, 0.0005)}
IS, OOS = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01")
TREND_EXITS = E.Exits(stop_atr=3.0, trail_atr=4.0)
BREAKOUT_EXITS = E.Exits(stop_atr=3.0)
RNG = np.random.default_rng(42)


def total(df, sig, exits, costs, period):
    a, b = period
    r = E.run(df.loc[a:b], sig[0].loc[a:b], sig[1].loc[a:b], exits, costs)
    m = E.metrics(r)
    return m["total_%"], m.get("profit_factor", 0.0), m["trades"]


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in df.values)


# ---------------- A. voisinage ----------------
def neighbourhood(frames):
    rows = []
    for tf in ("4h", "1D"):
        d = frames[tf]
        configs = []
        for f, s, t in itertools.product((10, 15, 20, 30), (40, 50, 75, 100), (None, 200)):
            configs.append(("EMA croisement", f"{f}/{s}" + (" +EMA200" if t else ""), S.ema_cross(d, f, s, t), TREND_EXITS))
        for n_in, n_out in itertools.product((20, 30, 40, 55, 80), (10, 15, 20, 30)):
            if n_out < n_in:
                configs.append(("Donchian", f"{n_in}/{n_out}", S.donchian(d, n_in, n_out), BREAKOUT_EXITS))
        for fam, name, sig, ex in configs:
            for fl, c in FEES.items():
                is_tot, is_pf, is_n = total(d, sig, ex, c, IS)
                oos_tot, oos_pf, oos_n = total(d, sig, ex, c, OOS)
                rows.append(dict(tf=tf, famille=fam, reglage=name, frais=fl, is_total=is_tot, is_pf=is_pf,
                                 is_trades=is_n, oos_total=oos_tot, oos_pf=oos_pf, oos_trades=oos_n))
    raw = pd.DataFrame(rows)
    g = raw.groupby(["tf", "famille", "frais"])
    summary = pd.DataFrame({
        "réglages testés": g.size(),
        "% positifs étude": (g.apply(lambda x: 100 * (x.is_total > 0).mean())).round(0),
        "% positifs contrôle": (g.apply(lambda x: 100 * (x.oos_total > 0).mean())).round(0),
        "% positifs les deux": (g.apply(lambda x: 100 * ((x.is_total > 0) & (x.oos_total > 0)).mean())).round(0),
        "contrôle : médiane %": g.oos_total.median().round(1),
        "contrôle : pire %": g.oos_total.min().round(1),
        "contrôle : meilleur %": g.oos_total.max().round(1),
        "contrôle : PF médian": g.oos_pf.median().round(2),
    }).reset_index()
    return raw, summary


# ---------------- B. année par année ----------------
REFS = [
    ("EMA 20/50 +EMA200", "4h", lambda d: S.ema_cross(d, 20, 50, 200), TREND_EXITS),
    ("Donchian 55/20", "4h", lambda d: S.donchian(d, 55, 20), BREAKOUT_EXITS),
    ("EMA 20/50 +EMA200", "1D", lambda d: S.ema_cross(d, 20, 50, 200), TREND_EXITS),
    ("Donchian 20/10", "1D", lambda d: S.donchian(d, 20, 10), BREAKOUT_EXITS),
]


def yearly(frames, costs):
    out = {}
    daily_close = frames["1D"]["close"]
    bh = daily_close.resample("YE").last() / daily_close.resample("YE").first() - 1
    out["Acheter et garder"] = (bh.values * 100).round(0)
    for name, tf, fn, ex in REFS:
        d = frames[tf]
        ent, xit = fn(d)
        r = E.run(d, ent, xit, ex, costs)
        eq = r.equity
        y_end = eq.resample("YE").last()
        y_start = pd.concat([pd.Series([1.0], index=[y_end.index[0]]), y_end.iloc[:-1]]).values
        out[f"{name} ({tf})"] = ((y_end.values / y_start - 1) * 100).round(0)
    df = pd.DataFrame(out, index=[str(i.year) for i in daily_close.resample("YE").last().index])
    df.index.name = "année"
    return df.reset_index()


# ---------------- C. Monte-Carlo ----------------
def monte_carlo(frames, costs, n_sims=10_000):
    rows = []
    for name, tf, fn, ex in REFS:
        d = frames[tf]
        ent, xit = fn(d)
        r = E.run(d, ent, xit, ex, costs)
        net = np.array([t.net for t in r.trades])
        years = (d.index[-1] - d.index[0]).days / 365.25
        n = max(1, int(round(len(net) / years * 2)))  # nombre de trades sur ~2 ans
        sims = RNG.choice(net, size=(n_sims, n), replace=True)
        eq = np.cumprod(1 + sims, axis=1)
        final = eq[:, -1]
        peak = np.maximum.accumulate(np.concatenate([np.ones((n_sims, 1)), eq], axis=1), axis=1)[:, 1:]
        mdd = (eq / peak - 1).min(axis=1)
        rows.append({
            "stratégie": f"{name} ({tf})",
            "trades / 2 ans": n,
            "proba de perdre sur 2 ans": f"{100 * (final < 1).mean():.0f} %",
            "résultat médian": f"{100 * (np.median(final) - 1):+.0f} %",
            "mauvais cas (5 %)": f"{100 * (np.percentile(final, 5) - 1):+.0f} %",
            "perte max médiane": f"{100 * np.median(mdd):.0f} %",
            "perte max mauvais cas (5 %)": f"{100 * np.percentile(mdd, 5):.0f} %",
        })
    return pd.DataFrame(rows)


def main():
    raw15 = data.load("15m")
    frames = {"4h": data.resample(raw15, "4h"), "1D": data.resample(raw15, "1D")}
    raw, summary = neighbourhood(frames)
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    raw.to_csv(os.path.join(res_dir, "exp02_voisinage_raw.csv"), index=False)

    lines = [f"# EXPÉRIENCE 02 — Robustesse du suivi de tendance ({date.today().isoformat()})", "",
             f"Données : BTC/EUR {raw15.index[0]:%Y-%m-%d} → {raw15.index[-1]:%Y-%m-%d}. "
             "Étude = 2020-2023, contrôle = 2024-2026. Glissement 0,05 % par ordre en plus des frais.", "",
             "## A. Voisinage des réglages", "",
             "Chaque ligne = une famille de stratégie testée avec tous ses réglages voisins.", "",
             md_table(summary), ""]
    for fl, c in FEES.items():
        lines += [f"## B. Année par année (rendement %, frais taker {fl})", "", md_table(yearly(frames, c)), ""]
    for fl, c in FEES.items():
        lines += [f"## C. Tirages aléatoires sur 2 ans (10 000 simulations, frais taker {fl})", "",
                  md_table(monte_carlo(frames, c)), ""]
    path = os.path.join(res_dir, "EXP02_robustesse.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print(path)


if __name__ == "__main__":
    main()
