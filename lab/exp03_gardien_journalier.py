"""
EXPÉRIENCE 03 — Préparer le « gardien de tendance journalier » (voie A choisie le 11/11/2026).

Questions :
  A. Le résultat tient-il sur un AUTRE marché (ETH/EUR) ? Sinon, c'est peut-être propre au BTC.
  B. Quelle SORTIE choisir ? On compare : signal seul / signal + stop 3 ATR / + trailing 4 ATR.
     Le stop est simulé comme un ordre stop posé chez Kraken (touché dès que le prix le traverse
     dans la journée) : c'est ce qui protégera la position même si le serveur redémarre.
  C. Quelle ENTRÉE ? « Croisement » (on n'achète qu'au moment du croisement) ou « état »
     (on achète dès que les conditions sont vraies, même en cours de tendance).
     C'est important pour savoir quand le bot achètera après sa mise en service.
  D. Où en est le signal AUJOURD'HUI (données Kraken en direct) ?

Frais : taker 0,40 % et 0,80 % (grille 2026 non confirmée), glissement 0,05 %.
Lancement : python lab/exp03_gardien_journalier.py
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402

import data  # noqa: E402
import engine as E  # noqa: E402
import strategies as S  # noqa: E402

FEES = {"0,40 %": E.Costs(0.0040, 0.0040, 0.0005), "0,80 %": E.Costs(0.0080, 0.0080, 0.0005)}
IS, OOS = ("2020-01-01", "2023-12-31"), ("2024-01-01", "2100-01-01")
EXITS = {
    "signal seul": E.Exits(),
    "signal + stop 3 ATR": E.Exits(stop_atr=3.0),
    "signal + stop 3 ATR + trailing 4 ATR": E.Exits(stop_atr=3.0, trail_atr=4.0),
}
ENTRIES = {"croisement": S.ema_cross, "état": S.ema_state}
GRID = list(itertools.product((10, 15, 20, 30), (40, 50, 75, 100)))


def md_table(df):
    cols = list(df.columns)
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in df.values)


def per_period(d, ent, xit, ex, c, period):
    a, b = period
    r = E.run(d.loc[a:b], ent.loc[a:b], xit.loc[a:b], ex, c)
    return E.metrics(r)


def study(markets):
    rows = []
    for mk, d in markets.items():
        for (en_name, fn), (ex_name, ex), (fl, c) in itertools.product(ENTRIES.items(), EXITS.items(), FEES.items()):
            res = []
            for f, s in GRID:
                ent, xit = fn(d, f, s, 200)
                mi, mo = per_period(d, ent, xit, ex, c, IS), per_period(d, ent, xit, ex, c, OOS)
                res.append((mi["total_%"], mo["total_%"], mo["max_dd_%"], mo.get("profit_factor", 0), mo["trades"]))
            df = pd.DataFrame(res, columns=["is", "oos", "dd", "pf", "n"])
            rows.append({
                "marché": mk, "entrée": en_name, "sortie": ex_name, "frais": fl,
                "% réglages gagnants (2 périodes)": round(100 * ((df["is"] > 0) & (df.oos > 0)).mean()),
                "contrôle médian %": round(df.oos.median(), 1),
                "contrôle pire %": round(df.oos.min(), 1),
                "perte max médiane %": round(df.dd.median(), 1),
                "PF médian": round(df.pf.median(), 2),
                "trades contrôle (médian)": int(df.n.median()),
            })
    return pd.DataFrame(rows)


def buy_hold(markets):
    rows = []
    for mk, d in markets.items():
        for p, (a, b) in (("étude", IS), ("contrôle", OOS)):
            m = E.buy_and_hold(d.loc[a:b], FEES["0,40 %"])
            rows.append({"marché": mk, "période": p, "total %": m["total_%"], "perte max %": m["max_dd_%"]})
    return pd.DataFrame(rows)


def kraken_daily(pair="XBTEUR"):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.load(r)
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    key = [k for k in payload["result"] if k != "last"][0]
    rows = payload["result"][key]
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "vwap", "volume", "count"])
    df.index = pd.to_datetime(df.t.astype(int), unit="s", utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df.iloc[:-1]  # la dernière bougie du jour n'est pas encore clôturée


def today_signal():
    out = []
    for name, pair in (("BTC/EUR", "XBTEUR"), ("ETH/EUR", "ETHEUR")):
        try:
            d = kraken_daily(pair)
        except Exception as e:  # réseau indisponible
            out.append(f"- {name} : données Kraken indisponibles ({e.__class__.__name__})")
            continue
        c = d.close
        e20, e50, e200 = (S.ema(c, n).iloc[-1] for n in (20, 50, 200))
        last = c.iloc[-1]
        state = (e20 > e50) and (last > e200)
        ent_x, _ = S.ema_cross(d, 20, 50, 200)
        last_cross = ent_x[ent_x].index.max()
        out.append(
            f"- **{name}** (clôture du {d.index[-1]:%d/%m/%Y}) : prix {last:,.0f} € · EMA20 {e20:,.0f} · "
            f"EMA50 {e50:,.0f} · EMA200 {e200:,.0f} → "
            + ("**conditions d'achat RÉUNIES**" if state else "**conditions d'achat NON réunies** (en euros, en attente)")
            + (f" · dernier croisement haussier valide : {last_cross:%d/%m/%Y}" if pd.notna(last_cross) else "")
        )
    return "\n".join(out).replace(",", " ")


def main():
    markets = {}
    for sym, label in (("BTCEUR", "BTC/EUR"), ("ETHEUR", "ETH/EUR")):
        markets[label] = data.resample(data.load("1h", sym=sym), "1D")
    res = study(markets)
    res_dir = os.path.join(HERE, "results"); os.makedirs(res_dir, exist_ok=True)
    lines = [f"# EXPÉRIENCE 03 — Gardien de tendance journalier ({date.today().isoformat()})", "",
             "Croisement de moyennes EMA (16 réglages voisins : rapide 10-30, lente 40-100) + filtre EMA200,",
             "bougies journalières. Étude 2020-2023, contrôle 2024 → fin octobre 2026.", "",
             "## Référence : acheter et garder", "", md_table(buy_hold(markets)), "",
             "## A/B/C. Marché × entrée × sortie × frais", "", md_table(res), "",
             "## D. Le signal aujourd'hui (données Kraken)", "", today_signal(), ""]
    path = os.path.join(res_dir, "EXP03_gardien_journalier.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print(path)


if __name__ == "__main__":
    main()
