"""
ORION GARDIEN — VRAIS ORDRES KRAKEN (préparés le 24/11/2026, VERROUILLÉS).

Ce module sait passer, chez Kraken, les trois ordres du gardien :
  - achat  : ordre LIMIT ;
  - vente  : ordre LIMIT ;
  - stop de protection : ordre « stop-loss » posé chez Kraken (il agit même si ORION est arrêté).

Il ne s'en sert aujourd'hui que pour l'ORDRE-TEST, en deux niveaux :
  1. « validation » : Kraken examine les ordres (format, droits de la clé, minimums) SANS les exécuter
     (option officielle validate=true). Aucun argent ne bouge. Utilisable dès maintenant.
  2. « réel » : un petit aller-retour réel (achat → stop posé puis retiré → vente), plafonné à 20 €,
     pour prouver que tout fonctionne en vrai. Coût ≈ 2 × 0,80 % de frais (≈ 0,25 € sur 15 €).
     Interdit tant que le propriétaire n'a pas réglé GARDIEN_ORDRE_TEST_AUTORISE=true sur Render (= son GO).

Règle de sécurité : si un achat réel a eu lieu, le module tente TOUJOURS de revendre ; s'il n'y arrive pas,
il le dit clairement (BTC restés sur le compte) et n'efface rien.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

SYMBOL = "BTC/EUR"
MAX_TEST_EUR = 20.0
TAKER_FEE = 0.008      # palier 1 Kraken Pro (ordre exécuté tout de suite)


def explain_kraken_error(exc: Exception) -> str:
    msg = str(exc)
    if "Permission denied" in msg:
        return "la clé API n'a pas le droit nécessaire (« Créer et modifier des ordres » / « Annuler et clôturer des ordres »)"
    if "Insufficient funds" in msg:
        return "fonds insuffisants sur Kraken"
    if "Order minimum not met" in msg or "volume minimum" in msg.lower():
        return "montant sous le minimum Kraken"
    if "Invalid key" in msg or "Invalid signature" in msg:
        return "clé API refusée par Kraken"
    if "Invalid nonce" in msg:
        return "décalage de « nonce » Kraken (deux programmes utilisent la même clé ?) : réessayer"
    return "erreur Kraken : " + msg[:160]


def _market(ex) -> dict:
    markets = ex.load_markets()
    return markets[SYMBOL]


def prepare_buy(ex, eur: float, fee: float = TAKER_FEE, cross: bool = True) -> dict:
    """Achat limit pour `eur` euros (frais compris). cross=True : prix au « ask » (exécution immédiate, taker) ;
    cross=False : prix au « bid » (ordre qui attend dans le carnet, maker 0,40 %)."""
    m, t = _market(ex), ex.fetch_ticker(SYMBOL)
    price = float(t["ask"] if cross else t["bid"])
    if cross:
        price *= 1.002  # petite marge pour être sûr d'être exécuté (on paie quand même le prix du carnet)
    price = float(ex.price_to_precision(SYMBOL, price))
    amount = float(ex.amount_to_precision(SYMBOL, eur / (price * (1 + fee))))
    _check_minimums(m, amount, price)
    return {"type": "limit", "side": "buy", "amount": amount, "price": price, "params": {} if cross else {"postOnly": True}}


def prepare_sell(ex, btc: float, cross: bool = True) -> dict:
    m, t = _market(ex), ex.fetch_ticker(SYMBOL)
    price = float(t["bid"] if cross else t["ask"])
    if cross:
        price *= 0.998
    price = float(ex.price_to_precision(SYMBOL, price))
    amount = float(ex.amount_to_precision(SYMBOL, btc))
    _check_minimums(m, amount, price)
    return {"type": "limit", "side": "sell", "amount": amount, "price": price, "params": {} if cross else {"postOnly": True}}


def prepare_stop(ex, btc: float, stop_price: float) -> dict:
    """Stop de protection posé chez Kraken : vente au marché déclenchée si le prix touche stop_price."""
    m = _market(ex)
    amount = float(ex.amount_to_precision(SYMBOL, btc))
    trigger = float(ex.price_to_precision(SYMBOL, stop_price))
    _check_minimums(m, amount, trigger)
    return {"type": "market", "side": "sell", "amount": amount, "price": None, "params": {"stopLossPrice": trigger}}


def _check_minimums(m: dict, amount: float, price: float) -> None:
    lim = m.get("limits") or {}
    amin = (lim.get("amount") or {}).get("min") or 0.0
    cmin = (lim.get("cost") or {}).get("min") or 0.0
    if amount <= 0 or amount < amin or amount * price < cmin:
        raise ValueError(f"montant sous le minimum Kraken ({amount} BTC ; minimum {amin} BTC et {cmin} €)")


def send(ex, spec: dict, validate: bool) -> dict:
    params = dict(spec["params"])
    if validate:
        params["validate"] = True  # Kraken vérifie l'ordre mais ne l'exécute pas
    return ex.create_order(SYMBOL, spec["type"], spec["side"], spec["amount"], spec["price"], params)


def wait_closed(ex, order_id: str, sleep: Callable = time.sleep, tries: int = 30, every: float = 2.0) -> dict:
    o: dict = {}
    for _ in range(tries):
        o = ex.fetch_order(order_id, SYMBOL)
        if o.get("status") in ("closed", "canceled", "expired", "rejected"):
            return o
        sleep(every)
    return o


def test_round_trip(ex, eur: float, real: bool, sleep: Callable = time.sleep) -> Dict:
    """ORDRE-TEST. real=False : validation Kraken seulement (aucun ordre exécuté)."""
    steps: List[dict] = []
    out = {"mode": "RÉEL (argent réel)" if real else "VALIDATION (aucun ordre exécuté)", "montant_eur": eur,
           "etapes": steps}

    def step(name, ok, detail, **extra):
        steps.append({"etape": name, "ok": ok, "detail": detail, **extra})

    if not (0 < eur <= MAX_TEST_EUR):
        step("Montant", False, f"le montant d'un ordre-test doit être entre 0 et {MAX_TEST_EUR:.0f} €")
        out["resultat"] = "REFUSÉ"
        return out

    try:
        buy = prepare_buy(ex, eur)
    except Exception as e:
        step("Préparation de l'achat", False, explain_kraken_error(e) if not isinstance(e, ValueError) else str(e))
        out["resultat"] = "ÉCHEC"
        return out
    step("Préparation de l'achat", True, f"achat limit de {buy['amount']} BTC à {buy['price']} €")

    if not real:
        ok_all = True
        for name, make in (("Achat (validation Kraken)", lambda: buy),
                           ("Stop de protection (validation Kraken)", lambda: prepare_stop(ex, buy["amount"], buy["price"] * 0.5)),
                           ("Vente (validation Kraken)", lambda: prepare_sell(ex, buy["amount"]))):
            try:
                send(ex, make(), validate=True)
                step(name, True, "Kraken accepte cet ordre (non exécuté)")
            except Exception as e:
                why = str(e) if isinstance(e, ValueError) else explain_kraken_error(e)
                funds = "fonds insuffisants" in why
                step(name, funds, ("format accepté ; " + why + " (normal avant le dépôt)") if funds else why)
                ok_all = ok_all and funds
        out["resultat"] = "OK" if ok_all else "ÉCHEC"
        return out

    # ---- RÉEL ----
    try:
        o = send(ex, buy, validate=False)
        o = wait_closed(ex, o["id"], sleep=sleep)
    except Exception as e:
        step("Achat réel", False, explain_kraken_error(e))
        out["resultat"] = "ÉCHEC (aucun BTC acheté)"
        return out
    filled = float(o.get("filled") or 0.0)
    if o.get("status") != "closed" or filled <= 0:
        try:
            ex.cancel_order(o["id"], SYMBOL)
        except Exception:
            pass
        filled = float(ex.fetch_order(o["id"], SYMBOL).get("filled") or 0.0)
        if filled <= 0:
            step("Achat réel", False, "ordre non exécuté à temps : annulé, aucun BTC acheté")
            out["resultat"] = "ÉCHEC (aucun BTC acheté)"
            return out
    cost = float(o.get("cost") or filled * buy["price"])
    step("Achat réel", True, f"{filled} BTC achetés pour {cost:.2f} €", frais=o.get("fee"))

    try:
        stop = send(ex, prepare_stop(ex, filled, buy["price"] * 0.5), validate=False)
        seen = any(x.get("id") == stop.get("id") for x in ex.fetch_open_orders(SYMBOL))
        ex.cancel_order(stop["id"], SYMBOL)
        step("Stop de protection posé puis retiré", True,
             "stop visible chez Kraken puis annulé" if seen else "stop accepté puis annulé")
    except Exception as e:
        step("Stop de protection posé puis retiré", False, explain_kraken_error(e) +
             " — vérifier sur Kraken qu'aucun ordre stop ne reste ouvert")

    try:
        s = send(ex, prepare_sell(ex, filled), validate=False)
        s = wait_closed(ex, s["id"], sleep=sleep)
        if s.get("status") != "closed":
            raise RuntimeError("vente non exécutée à temps (ordre laissé ouvert chez Kraken)")
        got = float(s.get("cost") or 0.0)
        step("Vente réelle", True, f"{s.get('filled')} BTC vendus pour {got:.2f} €", frais=s.get("fee"))
        out["resultat"] = "OK"
        out["cout_total_estime_eur"] = round(cost - got + (cost + got) * TAKER_FEE, 2)
    except Exception as e:
        step("Vente réelle", False, explain_kraken_error(e) if not isinstance(e, RuntimeError) else str(e))
        out["resultat"] = "ATTENTION : BTC restés sur le compte"
        out["a_faire"] = f"vendre {filled} BTC à la main sur Kraken (ou relancer), rien n'a été effacé"
    return out
