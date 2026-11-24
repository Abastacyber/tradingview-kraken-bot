"""
ORION GARDIEN — RÉCONCILIATION avec Kraken (22/11/2026). LECTURE SEULE : aucun ordre, aucune annulation.

Question posée : « ce qu'ORION croit avoir » (son portefeuille) correspond-il à « ce que Kraken dit » ?
  - position : si ORION croit détenir du BTC, Kraken doit en avoir au moins autant ;
  - euros    : si ORION est en euros, le solde EUR disponible doit couvrir son enveloppe ;
  - stop     : si ORION est en position, un ordre stop de vente doit être posé chez Kraken, de la bonne taille,
               au bon niveau ; s'il est en euros, aucun stop « orphelin » ne doit traîner ;
  - autres ordres ouverts sur BTC/EUR : signalés (ORION ne doit pas être surpris par un ordre qu'il ne connaît pas).

Chaque contrôle est OK, ATTENTION (à regarder, pas dangereux) ou BLOQUANT (le réel ne doit pas démarrer / continuer).
En simulation (DRY_RUN), le portefeuille d'ORION est fictif : la réconciliation sert alors de RÉPÉTITION —
elle vérifie l'accès à Kraken et dit ce qu'il manquerait pour passer en réel avec cette enveloppe.
"""
from __future__ import annotations

from typing import List, Optional

TOL = 0.005          # 0,5 % de tolérance (arrondis, frais pris en BTC)
MIN_BTC = 0.00006    # minimum d'ordre BTC/EUR chez Kraken : en dessous, c'est de la « poussière »


def _num(x) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bal(balance: dict, kind: str, cur: str) -> float:
    return _num((balance.get(kind) or {}).get(cur))


def _trigger(o: dict) -> Optional[float]:
    for k in ("triggerPrice", "stopPrice"):
        if o.get(k):
            return _num(o[k])
    info = o.get("info") or {}
    descr = info.get("descr") or {}
    if "stop" in str(descr.get("ordertype", o.get("type", ""))).lower():
        return _num(descr.get("price") or o.get("price"))
    return None


def _is_stop_sell(o: dict) -> bool:
    return o.get("side") == "sell" and (_trigger(o) is not None or "stop" in str(o.get("type", "")).lower())


def reconcile(pf: dict, balance: dict, open_orders: List[dict], simulation: bool) -> dict:
    checks: List[dict] = []

    def add(name: str, status: str, detail: str):
        checks.append({"controle": name, "statut": status, "detail": detail})

    btc_total, btc_free = _bal(balance, "total", "BTC"), _bal(balance, "free", "BTC")
    eur_free, eur_total = _bal(balance, "free", "EUR"), _bal(balance, "total", "EUR")
    stops = [o for o in open_orders if _is_stop_sell(o)]
    others = [o for o in open_orders if not _is_stop_sell(o)]
    in_pos = bool(pf.get("en_position"))
    want_btc = _num(pf.get("btc"))
    stop_level = pf.get("stop_protection")

    add("Accès Kraken (lecture des soldes et des ordres)", "OK", "la clé API répond")

    if in_pos:
        if btc_total >= want_btc * (1 - TOL):
            add("Position BTC", "OK", f"ORION croit détenir {want_btc:.8f} BTC ; Kraken en a {btc_total:.8f}")
        else:
            add("Position BTC", "BLOQUANT",
                f"ORION croit détenir {want_btc:.8f} BTC mais Kraken n'en a que {btc_total:.8f} : "
                "ne jamais continuer sans comprendre l'écart (vente manuelle ? ordre non exécuté ?)")
    else:
        if btc_total >= MIN_BTC:
            add("BTC hors enveloppe", "ATTENTION",
                f"ORION est en euros mais Kraken détient {btc_total:.8f} BTC : ORION n'y touchera pas "
                "(il ne vend que ce qu'il a acheté lui-même)")
        else:
            add("Position BTC", "OK", f"ORION est en euros ; Kraken : {btc_total:.8f} BTC (poussière ou rien)")
        need = _num(pf.get("euros"))
        if eur_free >= need * (1 - TOL):
            add("Euros pour l'enveloppe", "OK",
                f"enveloppe d'ORION : {need:.2f} € ; disponible chez Kraken : {eur_free:.2f} €")
        else:
            add("Euros pour l'enveloppe", "BLOQUANT",
                f"enveloppe d'ORION : {need:.2f} € mais seulement {eur_free:.2f} € disponibles chez Kraken "
                f"(il manquerait {need - eur_free:.2f} € pour acheter en réel)")

    if in_pos:
        if not stop_level:
            add("Stop de protection", "ATTENTION",
                "ORION n'a pas encore calculé son stop (il est posé après le premier jour clôturé suivant l'achat)")
        elif not stops:
            add("Stop de protection", "BLOQUANT",
                f"ORION attend un stop de vente vers {stop_level:.2f} € chez Kraken, mais aucun ordre stop n'est posé")
        else:
            s = max(stops, key=lambda o: _trigger(o) or 0.0)
            trig, amt = _trigger(s) or 0.0, _num(s.get("amount"))
            if amt < want_btc * (1 - TOL):
                add("Stop de protection", "BLOQUANT",
                    f"le stop chez Kraken ne couvre que {amt:.8f} BTC sur {want_btc:.8f}")
            elif trig < stop_level * (1 - TOL):
                add("Stop de protection", "ATTENTION",
                    f"stop chez Kraken à {trig:.2f} €, ORION le voudrait à {stop_level:.2f} € : il faut le remonter")
            else:
                add("Stop de protection", "OK", f"stop chez Kraken à {trig:.2f} € pour {amt:.8f} BTC")
            if len(stops) > 1:
                add("Stops en double", "ATTENTION", f"{len(stops)} ordres stop de vente ouverts : un seul est attendu")
    elif stops:
        add("Stop orphelin", "ATTENTION",
            f"ORION est en euros mais {len(stops)} ordre(s) stop de vente sont encore ouverts chez Kraken : à annuler")
    else:
        add("Stop de protection", "OK", "ORION est en euros : aucun stop attendu, aucun ouvert")

    if others:
        add("Autres ordres ouverts BTC/EUR", "ATTENTION",
            f"{len(others)} ordre(s) ouvert(s) qu'ORION ne gère pas : vérifier qu'ils sont voulus")

    statuses = {c["statut"] for c in checks}
    verdict = "BLOQUANT" if "BLOQUANT" in statuses else "ATTENTION" if "ATTENTION" in statuses else "OK"
    out = {
        "verdict": verdict,
        "pret_pour_le_reel": verdict != "BLOQUANT",
        "controles": checks,
        "orion_croit": {"en_position": in_pos, "btc": round(want_btc, 8), "euros": round(_num(pf.get("euros")), 2),
                        "stop_protection": round(stop_level, 2) if stop_level else None},
        "kraken_dit": {"btc_total": round(btc_total, 8), "btc_disponible": round(btc_free, 8),
                       "eur_total": round(eur_total, 2), "eur_disponible": round(eur_free, 2),
                       "ordres_ouverts_btc_eur": len(open_orders), "dont_stops_de_vente": len(stops)},
        "ordres": "aucun (lecture seule)",
    }
    if simulation:
        out["mode"] = ("SIMULATION : le portefeuille d'ORION est fictif, les écarts sont normaux. "
                       "Cette réconciliation est une répétition : elle vérifie l'accès à Kraken et indique "
                       "ce qu'il manquerait pour passer en réel avec cette enveloppe.")
    return out


def access_error(exc: Exception) -> dict:
    """Réponse quand Kraken refuse la lecture (clé absente, droit manquant, réseau)."""
    msg = str(exc)
    perm = any(w in msg for w in ("Permission denied", "EGeneral:Permission", "PermissionDenied"))
    auth = any(w in msg for w in ("Invalid key", "EAPI:Invalid", "AuthenticationError", "credentials"))
    if perm:
        detail = ("Kraken refuse la lecture : sur la clé API, cocher « Requête des fonds » (Query Funds) "
                  "et « Requête des ordres ouverts et des transactions » (Query Open Orders & Trades)")
    elif auth:
        detail = "clé API refusée par Kraken (clé ou secret incorrect sur Render)"
    else:
        detail = "Kraken injoignable pour l'instant ; réessayer plus tard"
    return {"verdict": "BLOQUANT", "pret_pour_le_reel": False,
            "controles": [{"controle": "Accès Kraken (lecture des soldes et des ordres)", "statut": "BLOQUANT",
                           "detail": detail}],
            "erreur_kraken": msg[:200], "ordres": "aucun (lecture seule)"}


# Explications SANS montant, pour les pages publiques (/health, /gardien/journal).
_HINTS = {
    "Position BTC": "Kraken détient moins de BTC qu'ORION ne le croit : ne rien faire avant d'avoir compris l'écart",
    "BTC hors enveloppe": "du BTC est présent sur Kraken en dehors de l'enveloppe d'ORION ; il n'y touchera pas",
    "Euros pour l'enveloppe": "le solde EUR de Kraken ne couvre pas l'enveloppe d'ORION (normal en simulation, avant le dépôt)",
    "Stop de protection": "le stop de protection chez Kraken est absent, trop petit ou trop bas",
    "Stops en double": "plusieurs ordres stop de vente sont ouverts : un seul est attendu",
    "Stop orphelin": "un ordre stop est resté ouvert alors qu'ORION est en euros : à annuler",
    "Autres ordres ouverts BTC/EUR": "des ordres BTC/EUR qu'ORION ne gère pas sont ouverts : vérifier qu'ils sont voulus",
}


def public_hint(check: dict) -> str:
    """Cause en clair d'un contrôle non OK, sans aucun montant (l'accès Kraken n'en contient jamais)."""
    if check.get("controle", "").startswith("Accès Kraken"):
        return check.get("detail", "")
    return _HINTS.get(check.get("controle", ""), "voir le détail sur /gardien/reconciliation (protégé)")
