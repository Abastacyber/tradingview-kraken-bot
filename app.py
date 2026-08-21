Texte collé(20260821-193339).txt
Document
 Je te donne à nouveau le feature/orion-protocol


Texte collé(20260821-194545).txt
Document
pour le moemment valids tu  
Texte collé(20260821-194955).txt
Document
Dis moi si mon code est OK pour le moment
Texte collé(20260821-195449).txt
Document
 et maintenant ? 

Si non donne moi les modifications exactes à effectuer
Texte collé(20260821-195955).txt
Document
On va étape par étape : et maintenant  ? 
Texte collé(20260821-200615).txt
Document
Texte collé(20260821-200804).txt
Document
OK et que penses tu de ce code ? 

Oui, ce code est nettement meilleur que la version précédente. Je viens aussi de vérifier le fichier complet : la syntaxe Python est valide, il compile correctement.

Mais je ne le considérerais pas encore comme validé pour le LIVE. Pour du DRY_RUN, on est proche. Il reste surtout 4 points importants, dont 2 critiques.

1. ✅ position_id : très bonne évolution

Cette partie est bien conçue :

current = _get_state_snapshot()
if current.get("position_id") != position_id or not current.get("has_position"):
    ...
    return

Un vieux thread voit désormais qu'il ne correspond plus à la position active et s'arrête. C'est exactement la logique que nous voulions.

Les ouvertures LONG et SHORT créent aussi chacune leur UUID et les fermetures remettent bien :

"position_id": None

Donc sur ce point : OK.

2. ⚠️ Le stop_price est enfin utilisé… mais pas exactement comme prévu

C'est une vraie amélioration :

real_sl_pct = _stop_pct_from_price(vwap, stop_price_val)
effective_sl_pct = real_sl_pct if real_sl_pct is not None else sl_pct

Donc le stop Pine n'est plus simplement jeté. ✅

Mais juste après :

effective_sl_pct = min(effective_sl_pct, MAX_SL_PCT, RISK_PCT)

Avec :

MAX_SL_PCT = 0.05
RISK_PCT = 0.02

ça signifie qu'un stop Pine situé à -4 % devient réellement un stop à -2 %.

Exemple :

Entrée réelle : 60 000 €
Stop Pine :     57 600 €   (-4 %)


ORION transforme :
effective_sl_pct = min(4 %, 5 %, 2 %)


=> stop réel = -2 %
=> 58 800 €

Donc on a amélioré le problème, mais ORION modifie encore silencieusement le stop Pine.

Je préfère notre principe initial : si Pine demande un stop valide à 4 %, ORION doit soit l'accepter, soit le refuser explicitement. Pas le transformer silencieusement.

3. 🔴 Problème plus important : un SELL Kraken peut échouer mais ORION efface quand même la position

Dans _monitor_trailing() :

try:
    ...
    if not DRY_RUN:
        ex.create_market_sell_order(symbol, q)
except Exception as e:
    log.warning("[TRAIL] SELL initial failed: %s", e)


_with_state(lambda s: (
    s.update({
        "has_position": False,
        ...
    })
))

C'est dangereux en LIVE.

Scénario :

Stop atteint
↓
ORION tente SELL
↓
Kraken refuse / timeout / API indisponible
↓
exception capturée
↓
ORION met quand même :
has_position = False
position_id = None
last_qty = 0

ORION croit alors que la position est fermée alors que les BTC peuvent toujours être sur Kraken.

Ça, je veux absolument le corriger avant le LIVE.

En DRY_RUN, aucun problème.

4. 🔴 Le position_id réduit le risque de double SELL, mais ne l'élimine pas complètement

Le thread fait :

current = _get_state_snapshot()


if current.get("position_id") != position_id:
    return

Puis il va chercher le ticker.

Entre ces deux moments, ceci peut arriver :

Thread trailing vérifie position A → OK
                  ↓
Webhook SELL ferme position A
                  ↓
Thread reçoit le ticker
                  ↓
stop atteint
                  ↓
create_market_sell_order()

Donc il manque encore une ultime vérification atomique immédiatement avant le SELL.

Nous avions justement évoqué _position_lock.

Par exemple :

with _position_lock:
    current = _get_state_snapshot()


    if (
        current.get("position_id") != position_id
        or not current.get("has_position")
    ):
        return


    # seulement maintenant : SELL

Ça empêchera un webhook SELL et le trailing de vendre simultanément.

5. Autre problème LIVE important que je veux garder dans notre radar

Ton SELL réel contient encore :

base_free = float(balances.get(base, 0.0))
...
qty_to_sell = max(0.0, base_free - BASE_RESERVE)

Donc en LIVE, ORION ne vend pas nécessairement la quantité de sa position.

Il vend potentiellement tout le BTC libre du compte Kraken.

Imaginons :

BTC déjà possédés personnellement : 0,1 BTC
ORION achète :                    0,0005 BTC

Un SELL ORION pourrait essayer de vendre environ :

0,1005 BTC

au lieu de :

0,0005 BTC

C'est un bloqueur LIVE majeur. À terme le SELL doit être basé sur la position suivie :

last_qty

avec réconciliation Kraken, pas sur la totalité de base_free.

6. Un autre comportement à décider

Actuellement, ORION peut refaire un BUY alors qu'un LONG existe déjà une fois le cooldown expiré.

Il n'y a pas de règle du genre :

if st.get("has_position") and st.get("position_side") == "long":
    return already_long

Le nouveau BUY écraserait alors :

last_qty
position_id
last_entry_price

alors que l'ancienne quantité BTC existe toujours.

Pour notre modèle actuel une seule position LONG ORION à la fois, il faudra bloquer ça.

Mon verdict

Je mettrais aujourd'hui :

Architecture : 8/10
DRY_RUN : presque prêt
LIVE : pas encore

Les améliorations position_id, utilisation du stop_price, nouvelle échelle de confidence et logs sont réellement bonnes.

Je ne changerais plus dix choses à la fois.

Prochaine modification unique que je ferais : sécuriser le SELL du trailing avec _position_lock et surtout ne jamais mettre has_position=False si l'ordre Kraken a échoué.

C'est maintenant le risque technique le plus important à éliminer. Ensuite on corrigera proprement le comportement exact du stop_price.

OK peux tu me proposer une version hydride entre ce que je t'ai envoyé et ce que tu m'as proposé ?

Oui. J’ai préparé une version hybride qui garde les bonnes idées de ton code tout en intégrant les sécurités que je voulais ajouter. Elle compile correctement en Python.

Elle conserve notamment ton position_id, ton stop_price Pine, le trailing, la logique DRY_RUN et ton architecture ORION actuelle.

J’ai ajouté/corrigé les points importants suivants : le stop Pine est maintenant utilisé comme prix absolu lorsqu’il reste cohérent avec le VWAP réel, sans être automatiquement resserré par RISK_PCT; le trailing re-vérifie la position sous _position_lock juste avant un SELL; si Kraken refuse le SELL, ORION garde la position ouverte; un ancien thread ne peut plus fermer une nouvelle position; un nouveau BUY est refusé si ORION est déjà LONG; et surtout, un SELL réel ne vend plus tout le BTC libre du compte, mais uniquement last_qty, c’est-à-dire la quantité suivie par ORION.

J’ai quand même gardé un fallback à 0,5 % si, après le fill, le stop envoyé par Pine devient incohérent. La réponse du BUY indique maintenant clairement ce qui a été utilisé avec :

"initial_stop": ...,
"sl_pct_used": ...,
"stop_source": "pine_stop"

ou un stop_source de type fallback_.... Ça nous permettra de vérifier immédiatement dans les tests si le stop ATR est réellement respecté.

Voici la version complète :

Télécharger app.py – version hybride sécurisée

Je te conseille de partir de celle-ci plutôt que des deux versions précédentes.

Et on garde notre méthode : ne la déploie pas directement en LIVE. La prochaine étape devrait être de la mettre sur feature/orion-protocol-v1, laisser DRY_RUN=true, puis faire un seul test BUY avec un stop_price volontairement identifiable pour vérifier que la réponse retourne bien exactement ce stop. 

app_orion_hybride_safe.py
Code
OK peux tu me proposer une version hydride entre ce que je t'ai envoyé et ce que tu m'as proposé ?  

Oui — c’est justement la version que je viens de préparer.

Elle combine ton code actuel avec les sécurités que je t’ai proposées, sans bouleverser toute l’architecture ORION.

👉 
Télécharger la version hybride de app.py

Elle conserve :

position_id
le stop_price envoyé par Pine
le trailing actuel
les profils de confiance existants
le fonctionnement DRY_RUN
l’architecture orion-v1

Et elle ajoute notamment :

arrêt des anciens threads de trailing ;
verrouillage juste avant un SELL ;
état conservé si Kraken refuse réellement la vente ;
SELL limité à la quantité ORION last_qty, pas à tous les BTC du compte ;
blocage d’un deuxième LONG si un LONG ORION existe déjà ;
utilisation réelle du stop Pine sans le transformer silencieusement avec RISK_PCT ;
retour de initial_stop, sl_pct_used et stop_source pour contrôler nos tests.

Je te recommande de prendre ce fichier comme nouvelle base feature/orion-protocol-v1.

Ensuite on ne change plus rien : on fait un seul test DRY_RUN BUY, avec un stop volontairement identifiable, et on vérifie ensemble le résultat avant de continuer.

app_orion_hybride_safe.py
Code

Bibliothèque
/
ORION PROJECT
/
app_orion_hybride_safe.py
import os
import json
import math
import time
import threading
import logging
import uuid

from collections import OrderedDict
from functools import lru_cache
from typing import Any, Dict, Tuple, Optional, Callable
from flask import Flask, request, jsonify
import ccxt


# ===== Helpers ENV =====
def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default

def env_float(name: str, default: float = 0.0) -> float:
    try: return float(env_str(name, str(default)))
    except: return float(default)

def env_int(name: str, default: int = 0) -> int:
    try: return int(float(env_str(name, str(default))))
    except: return int(default)

# ===== ENV =====
LOG_LEVEL              = env_str("LOG_LEVEL", "INFO").upper()
EXCHANGE_NAME          = env_str("EXCHANGE", "kraken").lower()
BASE_SYMBOL            = env_str("BASE_SYMBOL", "BTC").upper()
QUOTE_SYMBOL           = env_str("QUOTE_SYMBOL", "EUR").upper()
SYMBOL_DEFAULT         = f"{BASE_SYMBOL}/{QUOTE_SYMBOL}"
ORDER_TYPE             = env_str("ORDER_TYPE", "market").lower()

FIXED_QUOTE_PER_TRADE  = env_float("FIXED_QUOTE_PER_TRADE", 25.0)
MIN_QUOTE_PER_TRADE    = env_float("MIN_QUOTE_PER_TRADE", 15.0)
FEE_BUFFER_PCT         = env_float("FEE_BUFFER_PCT", 0.002)

BASE_RESERVE           = env_float("BASE_RESERVE", 0.0)
QUOTE_RESERVE          = env_float("QUOTE_RESERVE", 0.0)

RISK_PCT               = env_float("RISK_PCT", 0.02)
MAX_SL_PCT             = env_float("MAX_SL_PCT", 0.05)
BUY_COOL_SEC           = env_int("BUY_COOL_SEC", 300)

DRY_RUN                = env_str("DRY_RUN", "false").lower() in ("1","true","yes")
WEBHOOK_SECRET         = env_str("WEBHOOK_SECRET", env_str("WEBHOOK_TOKEN", ""))

TRAILING_ENABLED          = env_str("TRAILING_ENABLED","true").lower() in ("1","true","yes")
TRAIL_ACTIVATE_PCT_CONF2  = env_float("TRAIL_ACTIVATE_PCT_CONF2", 0.004)
TRAIL_GAP_CONF2           = env_float("TRAIL_GAP_CONF2", 0.002)
TRAIL_ACTIVATE_PCT_CONF3  = env_float("TRAIL_ACTIVATE_PCT_CONF3", 0.006)
TRAIL_GAP_CONF3           = env_float("TRAIL_GAP_CONF3", 0.003)

STATE_FILE             = env_str("STATE_FILE", "/tmp/bot_state.json")
RESTORE_ON_START       = env_str("RESTORE_ON_START","true").lower() in ("1","true","yes")

API_KEY                = env_str("KRAKEN_API_KEY", env_str("API_KEY",""))
API_SECRET             = env_str("KRAKEN_API_SECRET", env_str("API_SECRET",""))

KRAKEN_ENV             = env_str("KRAKEN_ENV","mainnet").lower()
KRAKEN_DEFAULT_TYPE    = env_str("KRAKEN_DEFAULT_TYPE","spot").lower()

BUY_SPLIT_CHUNKS       = max(1, env_int("BUY_SPLIT_CHUNKS", 1))
BUY_SPLIT_DELAY_MS     = max(0, env_int("BUY_SPLIT_DELAY_MS", 300))
SELL_SPLIT_CHUNKS      = max(1, env_int("SELL_SPLIT_CHUNKS", 1))

# --- Shorting (margin spot)
ENABLE_SHORTING        = env_str("ENABLE_SHORTING","false").lower() in ("1","true","yes")
MARGIN_LEVERAGE        = max(1, env_int("MARGIN_LEVERAGE", 2))
ALLOW_PAYLOAD_SYMBOL   = env_str("ALLOW_PAYLOAD_SYMBOL","false").lower() in ("1","true","yes")

# ===== ORION Protocol v1 =====
APP_VERSION = env_str("APP_VERSION", "1.0.0-dev")
ORION_PROTOCOL = env_str("ORION_PROTOCOL", "orion-v1")

ALERT_MAX_AGE_SEC = env_int("ALERT_MAX_AGE_SEC", 120)
MIN_CONFIDENCE = env_int("MIN_CONFIDENCE", 60)
MAX_PRICE_DEVIATION_PCT = env_float(
    "MAX_PRICE_DEVIATION_PCT",
    0.005,
)
MAX_SEEN_ALERTS = max(
    100,
    env_int("MAX_SEEN_ALERTS", 2000),
)

# ===== Logs/Flask/State =====
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("tv-kraken")
app = Flask(__name__)

_state_lock = threading.Lock()
_position_lock = threading.Lock()
_seen_alerts_lock = threading.Lock()
_seen_alerts = OrderedDict()
_state: Dict[str, Any] = {
    "has_position": False,         # True si long ouvert
    "last_buy_ts": 0.0,
    "last_entry_price": 0.0,
    "last_qty": 0.0,
    "position_side": "none",       # "none" | "long" | "short"
    "position_id": None,           # identifiant unique de la position courante
    "symbol": SYMBOL_DEFAULT,
}

def _now() -> float: return time.time()

def _save_state():
    try:
        with _state_lock: tmp = json.dumps(_state)
        with open(STATE_FILE, "w", encoding="utf-8") as f: f.write(tmp)
    except Exception as e:
        log.warning("STATE save error: %s", e)

def _load_state():
    if not RESTORE_ON_START: return
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f: data = json.load(f)
            with _state_lock: _state.update(data)
            log.info("STATE restored: %s", json.dumps(_state))
    except Exception as e:
        log.warning("STATE load error: %s", e)

def _with_state(mutator: Callable[[Dict[str, Any]], None]):
    with _state_lock:
        mutator(_state)
        snap = dict(_state)
    _save_state()
    return snap

def _get_state_snapshot() -> Dict[str, Any]:
    with _state_lock:
        return dict(_state)

# ===== Exchange helpers =====
def _assert_env():
    if EXCHANGE_NAME != "kraken":
        raise RuntimeError(f"Exchange non supporté: {EXCHANGE_NAME}")
    if not API_KEY or not API_SECRET:
        raise RuntimeError("KRAKEN_API_KEY / KRAKEN_API_SECRET manquants")

def _normalize_to_ccxt_symbol(s: str) -> str:
    if not s: return SYMBOL_DEFAULT
    s = s.replace("-", "/").upper()
    if "/" not in s:
        for q in ("USDT","USD","USDC","EUR","BTC","ETH"):
            if s.endswith(q):
                s = f"{s[:-len(q)]}/{q}"
                break
        else:
            return SYMBOL_DEFAULT
    base, quote = s.split("/")
    if base == "XBT": base = "BTC"
    return f"{base}/{quote}"

def _maybe_symbol_from_payload(payload_symbol: Optional[str]) -> str:
    if ALLOW_PAYLOAD_SYMBOL and payload_symbol:
        return _normalize_to_ccxt_symbol(payload_symbol)
    return _get_state_snapshot().get("symbol", SYMBOL_DEFAULT)

def _make_exchange():
    _assert_env()
    ex = ccxt.kraken({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": KRAKEN_DEFAULT_TYPE},
        "enableRateLimit": True,
    })
    if KRAKEN_ENV in ("testnet","sandbox","demo","paper","true","1","yes"):
        try: ex.set_sandbox_mode(True)
        except Exception: pass
    return ex

@lru_cache(maxsize=1)
def _load_markets(ex): return ex.load_markets()

def _amount_step_from_market(
    market: Dict[str, Any],
) -> Optional[float]:

    precision = (
        market.get("precision") or {}
    ).get("amount")

    if precision is not None:
        try:
            precision = float(precision)

            # CCXT peut fournir directement le pas minimal.
            if 0 < precision < 1:
                return precision

            # Compatibilité avec un format exprimé en décimales.
            if precision >= 1:
                return 10 ** (-int(precision))

        except (TypeError, ValueError, OverflowError):
            pass

    info = market.get("info") or {}

    for key in ("lotSz", "lotSize", "qtyStep", "minQty"):
        if key in info:
            try:
                value = float(info[key])
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue

    return None


def _get_min_trade_info(ex, symbol: str, price: float) -> Tuple[float, float, Optional[float]]:
    markets = _load_markets(ex)
    if symbol not in markets:
        raise RuntimeError(f"Symbole inconnu côté exchange: {symbol}")
    m = markets[symbol]
    limits = m.get("limits") or {}
    min_amount = float((limits.get("amount") or {}).get("min") or 0.0)
    min_cost   = float((limits.get("cost")   or {}).get("min") or 0.0)
    step       = _amount_step_from_market(m)
    if min_amount and price and (min_amount * price) > 200:
        log.warning("Ignoring absurd min_amount=%s (~%.2f %s)", min_amount, min_amount*price, symbol.split("/")[1])
        min_amount = 0.0
    return min_amount, min_cost, step

def _round_floor(value: float, step: float) -> float:
    if not step or step <= 0: return value
    return math.floor(value / step) * step

def _to_exchange_precision(ex, symbol: str, amount: float) -> float:
    try: return float(ex.amount_to_precision(symbol, amount))
    except: return amount

def _compute_base_qty_for_quote(ex, symbol: str, quote_amt: float) -> Tuple[float, float]:
    t = ex.fetch_ticker(symbol)
    price = float(t.get("last") or t.get("close") or t.get("ask") or t.get("bid") or 0.0)
    if price <= 0: raise RuntimeError("Prix invalide (ticker)")
    min_amount, min_cost, step = _get_min_trade_info(ex, symbol, price)
    qty = (quote_amt / price) * (1.0 - FEE_BUFFER_PCT)
    if min_cost and (qty * price) < min_cost: qty = min_cost / price
    if min_amount and qty < min_amount: qty = min_amount
    if step: qty = _round_floor(qty, step)
    if qty <= 0:
        required_quote = max(min_cost, (min_amount or 0)*price) or 0.0
        required_quote *= (1.0 + FEE_BUFFER_PCT)
        raise RuntimeError(f"Montant trop faible. Essaie >= ~{required_quote:.2f} {symbol.split('/')[1]}")
    return qty, price

def _tp_sl_from_confidence(conf: int) -> Tuple[float, float]:
    """
    Paramètres provisoires ORION v1.

    Confidence sur 0-100.
    tp_pct est conservé pour compatibilité, mais n'est pas encore utilisé.
    sl_pct sert uniquement de repli si le stop_price Pine est inutilisable.
    """
    conf = max(0, min(100, int(conf)))
    return 0.008, 0.005


def _trail_params(conf: int) -> Tuple[float, float]:
    """
    Trailing provisoire sur l'échelle ORION 0-100.

    60-84  : profil standard
    85-100 : forte confluence

    Le seuil 85 reste provisoire et devra être calibré par backtest.
    """
    conf = max(0, min(100, int(conf)))

    if conf >= 85:
        return (TRAIL_ACTIVATE_PCT_CONF3, TRAIL_GAP_CONF3)

    return (TRAIL_ACTIVATE_PCT_CONF2, TRAIL_GAP_CONF2)


def _resolve_initial_stop(
    entry_price: float,
    stop_price: Optional[float],
    fallback_sl_pct: float,
) -> Tuple[float, float, str]:
    """
    Détermine le stop initial réellement utilisé.

    Priorité :
      1) stop_price absolu envoyé par Pine s'il reste cohérent avec le VWAP réel ;
      2) fallback historique si le stop Pine devient incohérent après exécution.

    Aucun RISK_PCT n'est appliqué silencieusement au stop Pine.
    MAX_SL_PCT reste le garde-fou maximal.
    """
    fallback_sl_pct = min(max(float(fallback_sl_pct), 0.0), MAX_SL_PCT)
    fallback_stop = entry_price * (1.0 - fallback_sl_pct)

    if entry_price <= 0:
        return fallback_stop, fallback_sl_pct, "fallback_invalid_entry"

    try:
        sp = float(stop_price)
    except (TypeError, ValueError):
        return fallback_stop, fallback_sl_pct, "fallback_missing_stop"

    if not math.isfinite(sp) or sp <= 0 or sp >= entry_price:
        return fallback_stop, fallback_sl_pct, "fallback_incoherent_stop"

    distance_pct = (entry_price - sp) / entry_price

    if distance_pct <= 0 or distance_pct > MAX_SL_PCT:
        return fallback_stop, fallback_sl_pct, "fallback_stop_outside_guard"

    return sp, distance_pct, "pine_stop"


def _position_is_current(position_id: str) -> bool:
    current = _get_state_snapshot()
    return bool(
        current.get("has_position")
        and current.get("position_side") == "long"
        and current.get("position_id") == position_id
    )


def _clear_position_if_current(position_id: str):
    _with_state(lambda s: (
        s.update({
            "has_position": False,
            "position_side": "none",
            "position_id": None,
            "last_qty": 0.0,
        })
        if s.get("position_id") == position_id else None
    ))


# ===== Trailing (long only, simple) =====
def _monitor_trailing(
    symbol: str,
    qty: float,
    entry: float,
    conf: int,
    initial_stop: float,
    position_id: str,
):
    if not TRAILING_ENABLED or qty <= 0:
        return

    ex = _make_exchange()
    activate_pct, gap = _trail_params(conf)
    max_price = entry
    activated = False

    log.info(
        "[TRAIL] start %s qty=%.8f entry=%.2f stop=%.2f conf=%s position_id=%s",
        symbol, qty, entry, initial_stop, conf, position_id,
    )

    while True:
        if not _position_is_current(position_id):
            log.info(
                "[TRAIL] position périmée position_id=%s -> arrêt",
                position_id,
            )
            return

        try:
            t = ex.fetch_ticker(symbol)
            last = float(t.get("last") or t.get("close") or 0.0)

            if last <= 0:
                time.sleep(3)
                continue

            stop_to_use = initial_stop

            if not activated and last >= entry * (1.0 + activate_pct):
                activated = True
                max_price = max(max_price, last)
                log.info("[TRAIL] activated at %.2f", last)

            if activated:
                max_price = max(max_price, last)
                stop_to_use = max(
                    initial_stop,
                    max_price * (1.0 - gap),
                )

            if last <= stop_to_use:
                sell_reason = "initial_sl" if not activated else "trailing_sl"

                with _position_lock:
                    # Re-vérification atomique juste avant tout ordre SELL.
                    if not _position_is_current(position_id):
                        log.info(
                            "[TRAIL] position changée avant SELL position_id=%s -> annulation",
                            position_id,
                        )
                        return

                    try:
                        _, _, step = _get_min_trade_info(ex, symbol, last)
                        q = _round_floor(qty, step) if step else qty
                        q = _to_exchange_precision(ex, symbol, q)

                        if q <= 0:
                            raise RuntimeError("Quantité SELL trailing invalide")

                        if DRY_RUN:
                            log.info(
                                "[TRAIL] DRY_RUN SELL %s qty=%.8f last=%.2f stop=%.2f",
                                sell_reason, q, last, stop_to_use,
                            )
                        else:
                            ex.create_market_sell_order(symbol, q)

                    except Exception as e:
                        # Important : si Kraken refuse l'ordre, l'état reste ouvert.
                        log.exception(
                            "[TRAIL] SELL %s failed position_id=%s: %s",
                            sell_reason, position_id, e,
                        )
                        time.sleep(3)
                        continue

                    # On efface l'état uniquement après succès du SELL
                    # (ou simulation réussie en DRY_RUN).
                    _clear_position_if_current(position_id)

                log.info(
                    "[TRAIL] position closed reason=%s position_id=%s",
                    sell_reason, position_id,
                )
                break

            time.sleep(3)

        except Exception as e:
            log.warning("[TRAIL] error: %s", e)
            time.sleep(3)

    log.info("[TRAIL] finished position_id=%s", position_id)


def _remember_alert(alert_id: str) -> bool:
    """
    Enregistre un identifiant d'alerte.

    Retourne False si l'alerte a déjà été reçue.
    """
    with _seen_alerts_lock:
        if alert_id in _seen_alerts:
            return False

        _seen_alerts[alert_id] = time.time()

        while len(_seen_alerts) > MAX_SEEN_ALERTS:
            _seen_alerts.popitem(last=False)

        return True


def _validate_orion_payload(
    payload: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Valide un message ORION Protocol v1.

    Retourne :
        (True, "") si le message est valide.
        (False, "raison") si le message doit être refusé.
    """

    # 1. Vérification du protocole
    protocol = str(payload.get("protocol", "")).strip()

    if protocol != ORION_PROTOCOL:
        return False, "invalid_protocol"

    # 2. Vérification du signal
    signal = str(payload.get("signal", "")).strip().upper()

    if signal not in {"BUY", "SELL", "PING"}:
        return False, "invalid_signal"

    # PING est seulement un test de communication.
    # Il n'a pas besoin des informations d'un trade.
    if signal == "PING":
        return True, ""

    # 3. Vérification de l'identifiant unique
    alert_id = str(payload.get("alert_id", "")).strip()

    if not alert_id:
        return False, "missing_alert_id"

    if len(alert_id) > 160:
        return False, "invalid_alert_id"

    # 4. Vérification du symbole
    raw_symbol = str(payload.get("symbol", "")).strip()

    if not raw_symbol:
        return False, "missing_symbol"

    symbol = _normalize_to_ccxt_symbol(raw_symbol)

    if symbol != SYMBOL_DEFAULT:
        return False, "invalid_symbol"

    # 5. Vérification du timeframe
    timeframe = str(payload.get("timeframe", "")).strip()

    if timeframe != "15":
        return False, "invalid_timeframe"

    # 6. Vérification de la confiance
    try:
        confidence = int(payload.get("confidence"))
    except (TypeError, ValueError):
        return False, "invalid_confidence"

    if confidence < 0 or confidence > 100:
        return False, "invalid_confidence"

    if confidence < MIN_CONFIDENCE:
        return False, "confidence_too_low"

    # 7. Vérification du prix TradingView
    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return False, "invalid_price"

    if not math.isfinite(price) or price <= 0:
        return False, "invalid_price"


    # 8. Vérification de l'heure réelle d'envoi
    try:
        sent_at_ms = int(payload.get("sent_at"))
    except (TypeError, ValueError):
        return False, "invalid_sent_at"

    if sent_at_ms <= 0:
        return False, "invalid_sent_at"

    alert_time_sec = sent_at_ms / 1000.0
    alert_age_sec = time.time() - alert_time_sec

    if alert_age_sec > ALERT_MAX_AGE_SEC:
        return False, "stale_alert"

    # Tolérance de 30 secondes en cas de léger décalage d'horloge.
    if alert_age_sec < -30:
        return False, "future_alert"

    # 9. Vérification du Stop Loss pour un BUY
    if signal == "BUY":
        try:
            stop_price = float(payload.get("stop_price"))
        except (TypeError, ValueError):
            return False, "invalid_stop_price"

        if stop_price <= 0:
            return False, "invalid_stop_price"

        if stop_price >= price:
            return False, "stop_loss_above_entry"

        stop_distance_pct = (price - stop_price) / price

        if stop_distance_pct > MAX_SL_PCT:
            return False, "stop_loss_too_wide"

    return True, ""

# ===== ORION Decision Engine =====
def _orion_decide(
    payload: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Première couche du moteur de décision ORION.

    Retourne :
        ("execute", "reason") -> le trade peut continuer
        ("reject", "reason")  -> le trade doit être refusé
        ("wait", "reason")    -> réservé aux futures décisions ORION

    Cette version reste volontairement prudente.
    Elle prépare l'architecture du futur cerveau ORION
    sans encore remplacer la stratégie TradingView.
    """

    signal = str(
        payload.get("signal", "")
    ).strip().upper()

    confidence = int(
        payload.get("confidence", 0)
    )

    # PING n'est jamais une décision de trading.
    if signal == "PING":
        return "reject", "ping_not_trade"

    # Sécurité supplémentaire.
    if signal not in {"BUY", "SELL"}:
        return "reject", "unsupported_signal"

    # Première règle décisionnelle ORION.
    # Une confiance insuffisante ne doit jamais atteindre Kraken.
    if confidence < MIN_CONFIDENCE:
        return "reject", "confidence_below_orion_threshold"

    # Pour cette première version,
    # ORION autorise le trade après les contrôles précédents.
    return "execute", "orion_v1_pass"

# ===== Routes =====
@app.get("/")
def index():
    return jsonify({"service": "tv-kraken-bot", "status": "ok"}), 200

@app.get("/health")
def health():
    st = _get_state_snapshot()
    return jsonify({
        "status": "ok",
        "service": "orion",
        "version": APP_VERSION,
        "protocol": ORION_PROTOCOL,
        "symbol_default": SYMBOL_DEFAULT,
        "creds_ok": bool(API_KEY and API_SECRET),
        "secret_set": bool(WEBHOOK_SECRET),
        "dry_run": DRY_RUN,
        "shorting": ENABLE_SHORTING,
        "has_position": bool(st["has_position"]),
        "position_id": st.get("position_id"),
        "ts": int(time.time())
    }), 200

@app.get("/debug/balances")
def debug_balances():
    try:
        if WEBHOOK_SECRET:
            tok = (request.args.get("secret") or request.headers.get("X-Webhook-Token"))
            if tok != WEBHOOK_SECRET:
                return jsonify({"error": "unauthorized"}), 401
        ex = _make_exchange()
        b = ex.fetch_balance()
        return jsonify({"free": b.get("free", {}), "used": b.get("used", {}), "total": b.get("total", {})}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/webhook")
def webhook():
    with _position_lock:
        try:
            payload = request.get_json(silent=True) or {}
            if WEBHOOK_SECRET:
                tok = (payload.get("secret") or request.args.get("secret")
                       or payload.get("token") or request.args.get("token")
                       or request.headers.get("X-Webhook-Token"))
                if tok != WEBHOOK_SECRET:
                    log.error("Bad secret")
                    return jsonify({"error": "unauthorized"}), 401

            safe = dict(payload); safe.pop("secret", None); safe.pop("token", None)
            log.info("Webhook payload: %s", json.dumps(safe, ensure_ascii=False))
            is_valid, validation_error = _validate_orion_payload(payload)

            if not is_valid:
                log.warning(
                    "ORION payload rejected: %s",
                    validation_error,
                )
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": validation_error,
                    "protocol_expected": ORION_PROTOCOL,
                }), 400

            signal = str(
                payload.get("signal", "")
            ).strip().upper()

            # PING = simple test de communication.
            # Il ne doit pas être enregistré comme une alerte de trading.
            if signal == "PING":
                return jsonify({
                    "ok": True,
                    "pong": True,
                    "protocol": ORION_PROTOCOL,
                    "ts": int(time.time()),
                }), 200

            # Protection contre les alertes reçues plusieurs fois.
            alert_id = str(
                payload.get("alert_id", "")
            ).strip()

            if not _remember_alert(alert_id):
                log.warning(
                    "Duplicate ORION alert rejected: %s",
                    alert_id,
                )
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": "duplicate_alert",
                    "alert_id": alert_id,
                }), 409

            # Sécurité supplémentaire, même si le validateur
            # a déjà vérifié le signal.
            if signal not in {"BUY", "SELL"}:
                return jsonify({
                    "accepted": False,
                    "decision": "rejected",
                    "reason": "invalid_signal",
                    "allowed_signals": ["BUY", "SELL", "PING"],
                }), 400
            # ===== ORION Decision Gate =====
            decision, decision_reason = _orion_decide(payload)

            log.info(
                "ORION decision=%s reason=%s alert_id=%s",
                decision,
                decision_reason,
                alert_id,
            )

            if decision != "execute":
                return jsonify({
                    "accepted": True,
                    "decision": decision,
                    "reason": decision_reason,
                    "alert_id": alert_id,
                }), 200
            symbol = _maybe_symbol_from_payload(
                payload.get("symbol")
            )

            conf = int(
                payload.get("confidence")
                or payload.get("indicators_count")
                or 2
            )
            reason = str(payload.get("reason",""))[:160]
            tp_pct, sl_pct = _tp_sl_from_confidence(conf)

            ex = _make_exchange()
            _load_markets(ex)

            # ============= BUY (open long OR close short) =============
            if signal == "BUY":
                st = _get_state_snapshot()

                # ORION v1 : une seule position LONG suivie à la fois.
                if st.get("has_position") and st.get("position_side") == "long":
                    return jsonify({
                        "ok": False,
                        "skipped": "already_long",
                        "position_id": st.get("position_id"),
                    }), 200

                # Si short ouvert -> BUY ferme le short (quantité connue)
                if st.get("position_side") == "short" and st.get("last_qty", 0) > 0:
                    qty_to_buy = st["last_qty"]
                    qty_to_buy = _to_exchange_precision(ex, symbol, qty_to_buy)
                    if not DRY_RUN: order = ex.create_market_buy_order(symbol, qty_to_buy)
                    else: order = {"dry_run": True, "side":"buy", "qty": qty_to_buy}
                    _with_state(lambda s: s.update({
                        "has_position": False, "position_side": "none",
                        "position_id": None, "last_qty": 0.0,
                    }))
                    return jsonify({"ok": True, "side":"buy-close-short", "symbol": symbol,
                                    "amount": qty_to_buy, "order": order, "confidence": conf,
                                    "reason": reason}), 200

                # Sinon on ouvre un long (comme avant)
                now = _now()
                if _state.get("last_buy_ts", 0) and (now - _state["last_buy_ts"] < BUY_COOL_SEC):
                    wait = BUY_COOL_SEC - (now - _state["last_buy_ts"])
                    return jsonify({"ok": False, "reason":"buy_cooldown",
                                    "cooldown_remaining_sec": int(wait)}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({"error":"sizing_error",
                                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}"}), 400

                balances = ex.fetch_free_balance()
                avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
                usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
                quote_to_use = min(requested_quote, usable_quote)
                if quote_to_use <= 0:
                    return jsonify({"error":"insufficient_quote","available":avail_quote,
                                    "quote_reserve": QUOTE_RESERVE}), 400

                chunks = max(1, min(BUY_SPLIT_CHUNKS, 10))
                per_chunk_quote = quote_to_use / chunks
                total_qty, vw_cost = 0.0, 0.0
                last_price, orders = 0.0, []
                for i in range(chunks):
                    base_qty, price = _compute_base_qty_for_quote(ex, symbol, per_chunk_quote)
                    last_price = price
                    _, _, step = _get_min_trade_info(ex, symbol, price)
                    if step: base_qty = _round_floor(base_qty, step)
                    base_qty = _to_exchange_precision(ex, symbol, base_qty)
                    if DRY_RUN:
                        fill_price = price
                        order = {"dry_run":True, "side":"buy", "symbol":symbol, "qty":base_qty, "price":fill_price}
                    else:
                        order = ex.create_market_buy_order(symbol, base_qty)
                        fill_price = float(order.get("average") or order.get("price") or price)
                    total_qty += base_qty
                    vw_cost += base_qty * fill_price
                    orders.append(order)
                    if chunks > 1 and BUY_SPLIT_DELAY_MS > 0:
                        time.sleep(BUY_SPLIT_DELAY_MS/1000.0)

                vwap = (vw_cost / total_qty) if total_qty > 0 else last_price
                position_id = uuid.uuid4().hex

                # Stop réel : priorité au stop_price absolu envoyé par Pine.
                # ORION ne le resserre plus silencieusement avec RISK_PCT.
                initial_stop, effective_sl_pct, stop_source = _resolve_initial_stop(
                    vwap,
                    payload.get("stop_price"),
                    sl_pct,
                )

                log.info(
                    "[RISK] stop_source=%s entry=%.2f initial_stop=%.2f sl_pct=%.5f",
                    stop_source, vwap, initial_stop, effective_sl_pct,
                )

                _with_state(lambda s: s.update({
                    "has_position": True,
                    "position_side": "long",
                    "position_id": position_id,
                    "last_buy_ts": _now(),
                    "last_entry_price": vwap,
                    "last_qty": total_qty,
                    "symbol": symbol,
                }))

                if TRAILING_ENABLED and total_qty > 0:
                    threading.Thread(
                        target=_monitor_trailing,
                        args=(symbol, total_qty, vwap, conf, initial_stop, position_id),
                        daemon=True,
                    ).start()

                return jsonify({
                    "ok": True,
                    "side": "buy-open-long",
                    "symbol": symbol,
                    "amount": total_qty,
                    "avg_price": vwap,
                    "orders": orders,
                    "confidence": conf,
                    "reason": reason,
                    "position_id": position_id,
                    "initial_stop": initial_stop,
                    "sl_pct_used": effective_sl_pct,
                    "stop_source": stop_source,
                }), 200

            # ============= SELL (close long OR open short) =============
            if signal == "SELL":

                # En DRY_RUN, fermer d'abord la position longue simulée.
                if DRY_RUN:
                    st = dict(_state)

                    if (
                        st.get("position_side") == "long"
                        and st.get("has_position")
                        and st.get("last_qty", 0) > 0
                    ):
                        qty_to_sell = float(st["last_qty"])
                        order = {
                            "dry_run": True,
                            "side": "sell",
                            "symbol": symbol,
                            "qty": qty_to_sell,
                        }

                        _with_state(lambda s: s.update({
                            "has_position": False,
                            "position_side": "none",
                            "position_id": None,
                            "last_qty": 0.0,
                        }))

                        return jsonify({
                            "ok": True,
                            "side": "sell-close-long",
                            "symbol": symbol,
                            "amount": qty_to_sell,
                            "order": order,
                            "reason": reason,
                        }), 200

                # 1) Fermer uniquement la quantité de la position LONG suivie par ORION.
                st = _get_state_snapshot()
                if (
                    st.get("has_position")
                    and st.get("position_side") == "long"
                    and float(st.get("last_qty", 0.0)) > 0
                ):
                    ticker = ex.fetch_ticker(symbol)
                    price = float(ticker.get("last") or ticker.get("close") or 0.0) or 1.0
                    min_amount, _, step = _get_min_trade_info(ex, symbol, price)

                    qty_to_sell = float(st["last_qty"])
                    if step:
                        qty_to_sell = _round_floor(qty_to_sell, step)
                    qty_to_sell = _to_exchange_precision(ex, symbol, qty_to_sell)

                    if qty_to_sell < max(min_amount, 0.0):
                        return jsonify({
                            "ok": False,
                            "skipped": "tracked_qty_below_minimum",
                            "tracked_qty": st.get("last_qty"),
                            "min_amount": min_amount,
                        }), 200

                    try:
                        if DRY_RUN:
                            order = {
                                "dry_run": True,
                                "side": "sell",
                                "symbol": symbol,
                                "qty": qty_to_sell,
                            }
                        else:
                            order = ex.create_market_sell_order(symbol, qty_to_sell)
                    except Exception as e:
                        log.exception("SELL close long failed: %s", e)
                        return jsonify({
                            "ok": False,
                            "error": "sell_failed",
                            "detail": str(e),
                            "position_id": st.get("position_id"),
                        }), 502

                    _clear_position_if_current(st.get("position_id"))

                    return jsonify({
                        "ok": True,
                        "side": "sell-close-long",
                        "symbol": symbol,
                        "amount": qty_to_sell,
                        "order": order,
                        "reason": reason,
                    }), 200

                # 2) Sinon pas de BTC : ouvrir un short si autorisé
                if not ENABLE_SHORTING:
                    return jsonify({"ok": False, "skipped":"no_base_and_short_disabled"}), 200

                requested_quote = float(payload.get("quote") or FIXED_QUOTE_PER_TRADE)
                if requested_quote < MIN_QUOTE_PER_TRADE:
                    return jsonify({"error":"sizing_error",
                                    "detail": f"Montant trop faible: min {MIN_QUOTE_PER_TRADE} {QUOTE_SYMBOL}"}), 400

                # quantité à vendre (base) calibrée sur le "quote" et le levier
                base_qty, price = _compute_base_qty_for_quote(ex, symbol, requested_quote)
                # avec levier N, Kraken gère la marge; nous vendons "base_qty" et
                # on passe 'leverage' à l'API.
                _, _, step = _get_min_trade_info(ex, symbol, price)
                if step: base_qty = _round_floor(base_qty, step)
                base_qty = _to_exchange_precision(ex, symbol, base_qty)

                params = {"leverage": str(MARGIN_LEVERAGE)} if MARGIN_LEVERAGE else {}
                if DRY_RUN:
                    order = {"dry_run": True, "side":"sell", "symbol":symbol, "qty":base_qty, "leverage": MARGIN_LEVERAGE}
                else:
                    # create_order: type, side, amount, price=None, params={}
                    order = ex.create_order(symbol, "market", "sell", base_qty, None, params)

                short_position_id = uuid.uuid4().hex
                _with_state(lambda s: s.update({
                    "has_position": True,
                    "position_side": "short",
                    "position_id": short_position_id,
                    "last_entry_price": price,
                    "last_qty": base_qty,
                    "symbol": symbol,
                }))
                return jsonify({"ok": True, "side":"sell-open-short", "symbol": symbol,
                                "amount": base_qty, "order": order, "leverage": MARGIN_LEVERAGE,
                                "reason": reason, "position_id": short_position_id}), 200

            return jsonify({"error": f"unknown-signal:{signal}"}), 400

        except Exception as e:
            log.exception("webhook error")
            return jsonify({"error": str(e)}), 500

# ===== Boot =====
_load_state()

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
