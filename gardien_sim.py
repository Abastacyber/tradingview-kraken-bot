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

Étape 5 (22/11/2026) — STOP DE PROTECTION simulé (EXP06, option A choisie par le propriétaire) :
  - stop suiveur = plus haut depuis l'achat − 12 × ATR14 journalier, recalculé à chaque jour clôturé, jamais redescendu ;
  - simulé comme un ordre stop posé chez Kraken : touché dès que le prix le traverse (plus bas du jour),
    même si le serveur était arrêté (les journées manquées sont rattrapées avec les bougies clôturées) ;
  - vente par stop = ordre « taker » 0,80 % + 0,50 % de glissement (hypothèses prudentes d'EXP06) ;
  - pas de verrou : si les conditions d'achat sont toujours réunies, le gardien rachète (comme dans le labo).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Callable, List, Optional

import gardien

KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair={pair}"
DAY_SEC = 86400


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


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


def fetch_ticker(pair: str = "XBTEUR", opener: Callable = urllib.request.urlopen) -> dict:
    """Prix actuel + ouverture et plus bas DU JOUR (depuis 00:00 UTC) — API publique Kraken, sans clé."""
    with opener(KRAKEN_TICKER_URL.format(pair=pair), timeout=20) as r:
        payload = json.load(r)
    if payload.get("error"):
        raise RuntimeError(f"Kraken Ticker: {payload['error']}")
    data = next(iter(payload["result"].values()))
    out = {"prix": float(data["c"][0]), "ouverture_jour": float(data["o"]), "plus_bas_jour": float(data["l"][0])}
    if min(out.values()) <= 0:
        raise RuntimeError("ticker Kraken invalide")
    return out


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
        "plus_haut_depuis_achat": None,
        "stop_protection": None,
        "dernier_jour_stop": None,  # dernier jour clôturé déjà pris en compte pour le stop
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
        pf.update({"euros": 0.0, "btc": btc, "en_position": True, "prix_entree": price, "jour_entree": day,
                   "plus_haut_depuis_achat": price, "stop_protection": None, "dernier_jour_stop": day})
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
        _close_position(pf, eur)
        pf["frais_payes_eur"] += fee_eur
        op = {"type": "VENTE (simulée, ordre limit)", "jour": day, "le_utc": stamp, "prix": round(price, 2),
              "montant_eur": round(eur, 2), "frais_eur": round(fee_eur, 2), "resultat_net_trade_pct": perf}
        pf["trades"].append(op)
        return op
    return None


def _close_position(pf: dict, eur: float) -> None:
    pf.update({"euros": eur, "btc": 0.0, "en_position": False, "prix_entree": None, "jour_entree": None,
               "plus_haut_depuis_achat": None, "stop_protection": None})


def _last_buy_eur(pf: dict) -> Optional[float]:
    buys = [t for t in pf["trades"] if t["type"].startswith("ACHAT")]
    return buys[-1]["montant_eur"] if buys else None


def _stop_sell(pf: dict, fill: float, day: str, fee: float, slip: float, now: Optional[float], how: str) -> dict:
    """Vente simulée déclenchée par le stop posé chez Kraken."""
    now = time.time() if now is None else now
    px = fill * (1 - slip)
    gross = pf["btc"] * px
    fee_eur = gross * fee
    eur = gross - fee_eur
    stop = pf["stop_protection"]
    base = _last_buy_eur(pf)
    _close_position(pf, eur)
    pf["frais_payes_eur"] += fee_eur
    op = {"type": "VENTE PAR STOP (simulée, ordre stop Kraken)", "jour": day,
          "le_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), "stop": round(stop, 2),
          "prix": round(px, 2), "detail": how, "montant_eur": round(eur, 2), "frais_eur": round(fee_eur, 2),
          "resultat_net_trade_pct": round((eur / base - 1) * 100, 2) if base else None}
    pf["trades"].append(op)
    return op


def _stop_hit(pf: dict, open_: float, low: float):
    """Prix d'exécution si le stop est touché (ouverture si le marché ouvre déjà dessous), sinon None."""
    stop = pf.get("stop_protection")
    if not pf["en_position"] or not stop:
        return None
    if open_ <= stop:
        return open_, "ouverture sous le stop (trou de cotation)"
    if low <= stop:
        return stop, "plus bas du jour sous le stop"
    return None


def process_closed_days(pf: dict, candles: list, atrs: list, k: float, fee_stop: float, slip_stop: float,
                        now: Optional[float] = None) -> List[dict]:
    """Pour chaque jour CLÔTURÉ pas encore pris en compte depuis l'achat : 1) le stop en place a-t-il été touché
    (rattrapage si le serveur était arrêté) ? 2) sinon, remonter le stop avec ce jour (jamais le redescendre).
    candles : bougies (ts, ouverture, plus haut, plus bas, clôture) ; atrs : ATR14 de chaque bougie."""
    ops: List[dict] = []
    if not pf["en_position"]:
        return ops
    last = pf.get("dernier_jour_stop") or pf.get("jour_entree")
    start = len(candles)
    while start > 0 and (not last or _day(candles[start - 1][0]) > last):
        start -= 1  # on remonte seulement jusqu'au dernier jour déjà traité (rapide, même sur 7 ans)
    for cd, a in zip(candles[start:], atrs[start:]):
        day = _day(cd[0])
        o, h, l, _ = gardien.ohlc(cd)
        hit = _stop_hit(pf, o, l)
        if hit:
            ops.append(_stop_sell(pf, hit[0], day, fee_stop, slip_stop, now, hit[1]))
            pf["dernier_jour_stop"] = day
            break
        pf["plus_haut_depuis_achat"] = max(pf["plus_haut_depuis_achat"] or 0.0, h)
        new_stop = pf["plus_haut_depuis_achat"] - k * a
        if new_stop > 0:
            pf["stop_protection"] = max(pf["stop_protection"] or 0.0, new_stop)
        pf["dernier_jour_stop"] = day
    return ops


def check_intraday(pf: dict, ticker: dict, fee_stop: float, slip_stop: float, now: Optional[float] = None):
    """Jour EN COURS : le stop chez Kraken aurait-il été touché depuis 00:00 UTC (plus bas du jour) ?"""
    now = time.time() if now is None else now
    hit = _stop_hit(pf, ticker["ouverture_jour"], ticker["plus_bas_jour"])
    if not hit:
        return None
    day = _day(now)
    op = _stop_sell(pf, hit[0], day, fee_stop, slip_stop, now, hit[1] + " (jour en cours)")
    pf["dernier_jour_stop"] = day  # le jour en cours est réglé : pas de second traitement à sa clôture
    return op


def replay(candles: list, capital: float = 100.0, fee: float = 0.004, k: float = 12.0,
           fee_stop: float = 0.008, slip_stop: float = 0.005) -> dict:
    """REJEU HISTORIQUE avec le VRAI code du bot : chaque jour, juste après la clôture, le gardien
    1) vérifie/remonte son stop, 2) applique le signal au prix d'ouverture du lendemain.
    Sert à comparer le bot au laboratoire (mêmes trades attendus) et à tester des années de marché en virtuel."""
    ef, es, et, pos, _ = gardien._replay(candles)
    atrs = gardien.atr(candles)
    closes = [c[-1] for c in candles]
    pf = new_portfolio(capital, now=candles[0][0])
    for i in range(gardien.TREND, len(candles) - 1):
        now = candles[i][0] + DAY_SEC
        process_closed_days(pf, candles[: i + 1], atrs[: i + 1], k, fee_stop, slip_stop, now=now)
        signal = {"jour_cloture_utc": _day(candles[i][0]),
                  "position_theorique": "ACHETEUR (BTC)" if pos[i] else "EN EUROS",
                  "ema20_au_dessus_ema50": ef[i] > es[i], "prix_au_dessus_ema200": closes[i] > et[i]}
        apply_signal(pf, signal, candles[i + 1][1], fee, now=now)
    return pf


def summary(pf: dict, price: Optional[float]) -> dict:
    value = pf["euros"] + (pf["btc"] * price if price else 0.0)
    out = dict(pf)
    out["prix_actuel"] = round(price, 2) if price else None
    out["valeur_actuelle_eur"] = round(value, 2)
    out["resultat_depuis_debut_pct"] = round((value / pf["capital_initial_eur"] - 1) * 100, 2) if pf["capital_initial_eur"] else None
    out["frais_payes_eur"] = round(pf["frais_payes_eur"], 2)
    out["euros"] = round(pf["euros"], 2)
    stop = pf.get("stop_protection")
    out["stop_protection"] = round(stop, 2) if stop else None
    out["plus_haut_depuis_achat"] = round(pf["plus_haut_depuis_achat"], 2) if pf.get("plus_haut_depuis_achat") else None
    out["distance_au_stop_pct"] = round((price / stop - 1) * 100, 1) if (stop and price) else None
    out["btc"] = round(pf["btc"], 8)
    return out


def load(path: str):
    """Relit un fichier JSON du gardien. Absent → None.
    Illisible (fichier abîmé) → il est mis de côté sous un autre nom (jamais effacé) et None est renvoyé."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        try:
            os.replace(path, f"{path}.corrompu-{int(time.time())}")
        except OSError:
            pass
        return None


def save(path: str, data) -> None:
    """Écriture sûre : fichier temporaire puis remplacement (pas de fichier à moitié écrit en cas de coupure)."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def is_durable(path: str) -> bool:
    """Le stockage survit-il à un redéploiement Render ? /tmp est effacé, un disque persistant (ex. /var/data) non."""
    real = os.path.realpath(path)
    return not (real == "/tmp" or real.startswith("/tmp/"))
