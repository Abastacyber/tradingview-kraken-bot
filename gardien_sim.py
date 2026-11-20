"""
ORION GARDIEN v1 — étape 3 : portefeuille SIMULÉ (aucun ordre réel, quel que soit DRY_RUN).

Le gardien dispose d'une ENVELOPPE (GARDIEN_CAPITAL_EUR). Chaque jour clôturé, il compare sa position
simulée à la décision du signal journalier et simule l'ordre nécessaire :
  - achat  : seulement si les conditions d'achat sont réunies CE jour-là
             (EMA20 > EMA50 ET prix > EMA200) — pas d'achat « en rattrapage » au milieu d'une tendance
             qui ne remplit plus les conditions ;
  - vente  : dès que le signal repasse EN EUROS (EMA20 < EMA50).
Les ordres sont simulés comme des ordres LIMIT (maker) au prix Kraken du moment, frais 0,40 %
(grille Kraken Pro palier 1, vérifiée le 20/11/2026). Toute l'enveloppe est investie ou en euros.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Callable, Optional

KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair={pair}"


def fetch_price(pair: str = "XBTEUR", opener: Callable = urllib.request.urlopen) -> float:
    """Dernier prix BTC/EUR Kraken (API publique, sans clé)."""
    with opener(KRAKEN_TICKER_URL.format(pair=pair), timeout=20) as r:
        payload = json.load(r)
    if payload.get("error"):
        raise RuntimeError(f"Kraken Ticker: {payload['error']}")
    data = next(iter(payload["result"].values()))
    price = float(data["c"][0])
    if price <= 0:
        raise RuntimeError("prix Kraken invalide")
    return price


def new_portfolio(capital_eur: float, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    return {
        "mode": "simulation",
        "capital_initial_eur": round(capital_eur, 2),
        "euros": round(capital_eur, 8),
        "btc": 0.0,
        "en_position": False,
        "prix_entree": None,
        "jour_entree": None,
        "dernier_jour_traite": None,
        "frais_payes_eur": 0.0,
        "trades": [],
        "cree_le_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)),
    }


def apply_signal(pf: dict, signal: dict, price: float, fee: float, now: Optional[float] = None) -> Optional[dict]:
    """Met à jour le portefeuille simulé pour le jour clôturé du signal. Renvoie l'opération simulée ou None.
    Une seule décision par jour clôturé."""
    day = signal["jour_cloture_utc"]
    if pf.get("dernier_jour_traite") == day:
        return None
    pf["dernier_jour_traite"] = day
    now = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))
    want_long = signal["position_theorique"].startswith("ACHETEUR")
    buy_ok = signal["ema20_au_dessus_ema50"] and signal["prix_au_dessus_ema200"]

    if not pf["en_position"] and want_long and buy_ok and pf["euros"] > 0:
        eur = pf["euros"]
        fee_eur = eur * fee
        btc = (eur - fee_eur) / price
        pf.update({"euros": 0.0, "btc": btc, "en_position": True, "prix_entree": price, "jour_entree": day})
        pf["frais_payes_eur"] += fee_eur
        op = {"type": "ACHAT (simulé, ordre limit)", "jour": day, "le_utc": stamp, "prix": round(price, 2),
              "montant_eur": round(eur, 2), "btc": round(btc, 8), "frais_eur": round(fee_eur, 2)}
        pf["trades"].append(op)
        return op

    if pf["en_position"] and not want_long:
        gross = pf["btc"] * price
        fee_eur = gross * fee
        eur = gross - fee_eur
        perf = None
        buys = [t for t in pf["trades"] if t["type"].startswith("ACHAT")]
        if buys:
            perf = round((eur / buys[-1]["montant_eur"] - 1) * 100, 2)
        pf.update({"euros": eur, "btc": 0.0, "en_position": False, "prix_entree": None, "jour_entree": None})
        pf["frais_payes_eur"] += fee_eur
        op = {"type": "VENTE (simulée, ordre limit)", "jour": day, "le_utc": stamp, "prix": round(price, 2),
              "montant_eur": round(eur, 2), "frais_eur": round(fee_eur, 2), "resultat_net_trade_pct": perf}
        pf["trades"].append(op)
        return op
    return None


def summary(pf: dict, price: Optional[float]) -> dict:
    value = pf["euros"] + (pf["btc"] * price if price else 0.0)
    out = dict(pf)
    out["prix_actuel"] = round(price, 2) if price else None
    out["valeur_actuelle_eur"] = round(value, 2)
    out["resultat_depuis_debut_pct"] = round((value / pf["capital_initial_eur"] - 1) * 100, 2) if pf["capital_initial_eur"] else None
    out["frais_payes_eur"] = round(pf["frais_payes_eur"], 2)
    out["euros"] = round(pf["euros"], 2)
    out["btc"] = round(pf["btc"], 8)
    return out


def load(path: str) -> Optional[dict]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def save(path: str, pf: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False)
    os.replace(tmp, path)
