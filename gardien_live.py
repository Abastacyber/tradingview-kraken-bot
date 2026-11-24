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


# =====================================================================================================
# GARDIEN AUTOMATIQUE RÉEL (branché le 24/11/2026) — actif SEULEMENT si DRY_RUN=false ET GARDIEN_REEL=true
# (double verrou, réglé par le propriétaire = son GO) et si la réconciliation avec Kraken le permet.
#
# À chaque vérification de l'horloge (toutes les heures) :
#   1. ordre en cours (achat/vente limit) : exécuté ? → on met à jour le portefeuille réel ;
#      pas encore → on le replace au meilleur prix du carnet (ordre « maker », 0,40 %) ;
#      trop long (achat 12 h, vente 3 h, réglables) → on passe en ordre immédiat (« taker », 0,80 %).
#   2. stop de protection chez Kraken : exécuté ? → la position est close (vente par stop) ;
#   3. décision du jour clôturé (même règle que la simulation et le labo) : achat ou vente ;
#   4. stop : remonté chaque jour (plus haut − 12 ATR, jamais redescendu) et tenu à jour chez Kraken.
# Ouvrir une position exige la réconciliation complète (verrou) ; se PROTÉGER (stop, vente) reste permis
# tant que Kraken est lisible et que la position concorde.
# =====================================================================================================

MAKER_FEE = 0.004
_SAVE: Optional[Callable] = None  # enregistrement immédiat du portefeuille réel après CHAQUE action chez Kraken


def _persist(pf: dict) -> None:
    """Un ordre passé mais non enregistré pourrait être repassé : on enregistre tout de suite."""
    if _SAVE:
        _SAVE(pf)
STOP_RAISE_MIN = 0.002   # on ne déplace le stop chez Kraken que s'il monte d'au moins 0,2 %


def new_live_portfolio(capital_eur: float, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    return {"mode": "réel", "capital_initial_eur": round(capital_eur, 2), "euros": round(capital_eur, 8), "btc": 0.0,
            "en_position": False, "prix_entree": None, "jour_entree": None, "dernier_jour_traite": None,
            "dernier_jour_stop": None, "plus_haut_depuis_achat": None, "stop_protection": None,
            "stop_order_id": None, "stop_niveau_kraken": None, "ordre_en_cours": None,
            "frais_payes_eur": 0.0, "trades": [], "evenements": [],
            "cree_le_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))}


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))


def _event(pf: dict, now: float, text: str) -> None:
    pf.setdefault("evenements", []).append({"le_utc": _stamp(now), "detail": text})
    pf["evenements"] = pf["evenements"][-100:]


def _fee_eur(o: dict, price: float) -> float:
    fee = o.get("fee") or {}
    cost = float(fee.get("cost") or 0.0)
    return cost * price if fee.get("currency") == "BTC" else cost


def _apply_fill(pf: dict, o: dict, side: str, now: float, label: str) -> Optional[dict]:
    """Met à jour le portefeuille réel avec la partie EXÉCUTÉE d'un ordre Kraken (appelée une seule fois par ordre)."""
    filled = float(o.get("filled") or 0.0)
    if filled <= 0:
        return None
    price = float(o.get("average") or o.get("price") or 0.0)
    cost = float(o.get("cost") or filled * price)
    if not price:
        price = cost / filled
    fee_info = o.get("fee") or {}
    fee_btc = float(fee_info.get("cost") or 0.0) if fee_info.get("currency") == "BTC" else 0.0
    fee_eur = float(fee_info.get("cost") or 0.0) if fee_info.get("currency") != "BTC" else fee_btc * price
    pf["frais_payes_eur"] = pf.get("frais_payes_eur", 0.0) + fee_eur
    trade = {"type": label, "le_utc": _stamp(now), "prix": round(price, 2), "btc": round(filled, 8),
             "frais_eur": round(fee_eur, 2), "ordre_kraken": o.get("id")}
    if side == "buy":
        paid = cost + (fee_eur if not fee_btc else 0.0)
        pf["euros"] = max(0.0, pf["euros"] - paid)
        pf["btc"] += filled - fee_btc
        if not pf["en_position"]:
            pf.update({"en_position": True, "prix_entree": price, "plus_haut_depuis_achat": price,
                       "stop_protection": None, "cout_position_eur": 0.0, "recu_position_eur": 0.0})
        pf["cout_position_eur"] = pf.get("cout_position_eur", 0.0) + paid
        trade["montant_eur"] = round(paid, 2)
    else:
        got = cost - (fee_eur if not fee_btc else 0.0)
        pf["btc"] = max(0.0, pf["btc"] - filled - fee_btc)
        pf["euros"] += got
        pf["recu_position_eur"] = pf.get("recu_position_eur", 0.0) + got
        trade["montant_eur"] = round(got, 2)
        if pf["btc"] * price < 0.45:  # position soldée (il ne reste que de la poussière)
            if pf.get("cout_position_eur"):
                trade["resultat_net_trade_pct"] = round((pf["recu_position_eur"] / pf["cout_position_eur"] - 1) * 100, 2)
            pf.update({"en_position": False, "prix_entree": None, "jour_entree": None, "plus_haut_depuis_achat": None,
                       "stop_protection": None, "cout_position_eur": 0.0, "recu_position_eur": 0.0})
    pf["trades"].append(trade)
    return trade


def _place(ex, pf: dict, side: str, now: float, taker: bool, can: bool, started: Optional[float] = None) -> Optional[dict]:
    """Place l'achat (tous les euros de l'enveloppe) ou la vente (tous les BTC de l'enveloppe)."""
    what = "achat" if side == "buy" else "vente"
    if not can:
        _event(pf, now, f"{what} non placé : verrou fermé (voir la réconciliation)")
        return None
    try:
        spec = (prepare_buy(ex, pf["euros"], fee=TAKER_FEE if taker else MAKER_FEE, cross=taker) if side == "buy"
                else prepare_sell(ex, pf["btc"], cross=taker))
        o = send(ex, spec, validate=False)
    except Exception as e:
        _event(pf, now, f"{what} refusé : " + (str(e) if isinstance(e, ValueError) else explain_kraken_error(e)))
        return None
    pf["ordre_en_cours"] = {"id": o["id"], "side": side, "debut_ts": started or now, "prix": spec["price"], "taker": taker}
    _persist(pf)
    _event(pf, now, f"ordre {what} {'immédiat (taker)' if taker else 'limit (maker)'} placé : {spec['amount']} BTC à {spec['price']} €")
    return pf["ordre_en_cours"]


def _handle_pending(ex, pf: dict, now: float, can_open: bool, can_protect: bool,
                    buy_hours: float, sell_hours: float) -> List[dict]:
    """Ordre limit en cours : s'il n'est pas (entièrement) exécuté, on l'annule, on compte la partie exécutée,
    puis on replace le reste au meilleur prix (maker) — ou en ordre immédiat (taker) une fois le délai dépassé."""
    pend = pf.get("ordre_en_cours")
    if not pend:
        return []
    o = ex.fetch_order(pend["id"], SYMBOL)
    if o.get("status") == "open":
        try:
            ex.cancel_order(pend["id"], SYMBOL)
        except Exception as e:  # on réessaiera à la prochaine vérification ; rien n'est compté deux fois
            _event(pf, now, "annulation pour replacement impossible : " + explain_kraken_error(e))
            return []
        o = ex.fetch_order(pend["id"], SYMBOL)
        if o.get("status") == "open":
            return []
    side = pend["side"]
    label = ("ACHAT (réel, " if side == "buy" else "VENTE (réelle, ") + ("immédiat)" if pend.get("taker") else "limit)")
    t = _apply_fill(pf, o, side, now, label)
    pf["ordre_en_cours"] = None
    _persist(pf)
    rest = pf["euros"] if side == "buy" else pf["btc"] * float(pend.get("prix") or 0.0)
    if rest >= 1.0:
        taker = bool(pend.get("taker")) or (now - pend["debut_ts"]) >= (buy_hours if side == "buy" else sell_hours) * 3600
        _place(ex, pf, side, now, taker=taker, can=can_open if side == "buy" else can_protect, started=pend["debut_ts"])
    return [t] if t else []


def _check_stop_order(ex, pf: dict, now: float) -> List[dict]:
    sid = pf.get("stop_order_id")
    if not sid:
        return []
    o = ex.fetch_order(sid, SYMBOL)
    if o.get("status") == "closed" or float(o.get("filled") or 0) > 0:
        pf.update({"stop_order_id": None, "stop_niveau_kraken": None})
        t = _apply_fill(pf, o, "sell", now, "VENTE PAR STOP (réelle, ordre stop Kraken)")
        _persist(pf)
        _event(pf, now, "le stop de protection a été exécuté par Kraken")
        return [t] if t else []
    if o.get("status") in ("canceled", "expired", "rejected"):
        pf.update({"stop_order_id": None, "stop_niveau_kraken": None})
        _event(pf, now, "le stop chez Kraken a disparu (annulé ou expiré) : il va être reposé")
    return []


def _update_stop_level(pf: dict, candles: list, atrs: list, k: float) -> None:
    import gardien
    if not pf["en_position"]:
        return
    last = pf.get("dernier_jour_stop") or pf.get("jour_entree")
    for cd, a in zip(candles, atrs):
        day = time.strftime("%Y-%m-%d", time.gmtime(cd[0]))
        if last and day <= last:
            continue
        _, h, _, _ = gardien.ohlc(cd)
        pf["plus_haut_depuis_achat"] = max(pf.get("plus_haut_depuis_achat") or 0.0, h)
        new = pf["plus_haut_depuis_achat"] - k * a
        if new > 0:
            pf["stop_protection"] = max(pf.get("stop_protection") or 0.0, new)
        pf["dernier_jour_stop"] = day


def _sync_stop(ex, pf: dict, now: float, can_protect: bool) -> None:
    """Le stop chez Kraken suit le stop calculé (jamais vers le bas)."""
    want = pf.get("stop_protection")
    if not (pf["en_position"] and want and can_protect) or pf.get("ordre_en_cours"):
        return
    have = pf.get("stop_niveau_kraken")
    if pf.get("stop_order_id") and have and want <= have * (1 + STOP_RAISE_MIN):
        return
    try:
        spec = prepare_stop(ex, pf["btc"], want)
        if pf.get("stop_order_id"):
            ex.cancel_order(pf["stop_order_id"], SYMBOL)  # Kraken réserve les BTC : on retire l'ancien, puis on repose
            pf.update({"stop_order_id": None, "stop_niveau_kraken": None})
            _persist(pf)
        o = send(ex, spec, validate=False)
        pf.update({"stop_order_id": o["id"], "stop_niveau_kraken": spec["params"]["stopLossPrice"]})
        _persist(pf)
        _event(pf, now, f"stop de protection posé chez Kraken à {spec['params']['stopLossPrice']} € pour {spec['amount']} BTC")
    except Exception as e:
        _event(pf, now, "stop de protection NON posé : " + (str(e) if isinstance(e, ValueError) else explain_kraken_error(e)))


def live_step(ex, pf: dict, signal: dict, candles: list, atrs: list, k: float, can_open: bool, can_protect: bool,
              now: Optional[float] = None, buy_hours: float = 12.0, sell_hours: float = 3.0,
              save: Optional[Callable] = None) -> List[dict]:
    """Une vérification du gardien RÉEL. Renvoie les opérations exécutées chez Kraken.
    save(pf) est appelé après chaque action chez Kraken (enregistrement immédiat)."""
    global _SAVE
    _SAVE = save
    try:
        return _live_step(ex, pf, signal, candles, atrs, k, can_open, can_protect,
                          time.time() if now is None else now, buy_hours, sell_hours)
    finally:
        _SAVE = None


def _live_step(ex, pf, signal, candles, atrs, k, can_open, can_protect, now, buy_hours, sell_hours) -> List[dict]:
    ops: List[dict] = []
    ops += _check_stop_order(ex, pf, now)
    ops += _handle_pending(ex, pf, now, can_open, can_protect, buy_hours, sell_hours)

    day = signal["jour_cloture_utc"]
    if pf.get("dernier_jour_traite") != day and not pf.get("ordre_en_cours"):
        want_long = signal["position_theorique"].startswith("ACHETEUR")
        buy_ok = signal["ema20_au_dessus_ema50"] and signal["prix_au_dessus_ema200"]
        if not pf["en_position"] and want_long and buy_ok and pf["euros"] >= 1.0:
            if _place(ex, pf, "buy", now, taker=False, can=can_open):
                pf["jour_entree"] = day
                pf["dernier_jour_stop"] = day
                pf["dernier_jour_traite"] = day
        elif pf["en_position"] and not want_long:
            if pf.get("stop_order_id") and can_protect:
                try:
                    ex.cancel_order(pf["stop_order_id"], SYMBOL)
                    pf.update({"stop_order_id": None, "stop_niveau_kraken": None})
                    _persist(pf)
                except Exception as e:
                    _event(pf, now, "retrait du stop avant la vente impossible : " + explain_kraken_error(e))
            if not pf.get("stop_order_id") and _place(ex, pf, "sell", now, taker=False, can=can_protect):
                pf["dernier_jour_traite"] = day
        else:
            pf["dernier_jour_traite"] = day

    _update_stop_level(pf, candles, atrs, k)
    _sync_stop(ex, pf, now, can_protect)
    return ops
