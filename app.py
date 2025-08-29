import os
import time
import math
import logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
from binance.client import Client
from binance.error import ClientError

# ====================
# Configuration
# ====================
# Initialisation de l'application Flask et du logger
app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("tv-binance-bot")

# Variables d'environnement
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
# La passphrase est pour l'API de futures. Le bot actuel est pour le spot.
API_PASSPHRASE = os.getenv("BINANCE_API_PASSPHRASE", "") 
SYMBOL_DEFAULT = os.getenv("SYMBOL", "BTCUSDT").upper()
SIZE_MODE = os.getenv("SIZE_MODE", "fixed_quote").lower()
FIXED_QUOTE_PER_TRADE = Decimal(os.getenv("FIXED_QUOTE_PER_TRADE", "20"))
FIXED_QTY_PER_TRADE = Decimal(os.getenv("FIXED_QTY_PER_TRADE", "0.001"))
FALLBACK_TP_PCT = Decimal(os.getenv("FALLBACK_TP_PCT", "0.8"))
FALLBACK_SL_PCT = Decimal(os.getenv("FALLBACK_SL_PCT", "0.5"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "5"))
_last_order_ts = 0

# Initialisation du client Binance Spot
client = Binance(api_key=API_KEY, api_secret=API_SECRET)

# ====================
# Fonctions utilitaires
# ====================
def get_filters(symbol: str):
    """
    Récupère les filtres d'échange pour un symbole.
    Cette fonction est critique pour s'assurer que les ordres respectent les contraintes de Binance.
    """
    try:
        info = client.exchange_info(symbol=symbol)
        filters = info["symbols"][0]["filters"]
        price_filter = next(x for x in filters if x["filterType"] == "PRICE_FILTER")
        lot_filter = next(x for x in filters if x["filterType"] == "LOT_SIZE")
        
        return Decimal(price_filter["tickSize"]), Decimal(lot_filter["stepSize"])
    except Exception as e:
        log.error(f"Erreur lors de la récupération des filtres pour le symbole {symbol}: {e}")
        raise

def quantize(value: Decimal, step: Decimal) -> Decimal:
    """Coupe une valeur à l’incrément de pas le plus proche pour respecter les règles de Binance."""
    if step == 0:
        return value
    precision = max(0, -step.as_tuple().exponent)
    return value.quantize(step.normalize(), rounding=ROUND_DOWN)

def get_avg_fill_price(order_resp) -> Decimal:
    """Calcule le prix moyen d'exécution d'un ordre, en utilisant les 'fills' si disponibles."""
    if "fills" in order_resp and order_resp["fills"]:
        total_price = sum(Decimal(f["price"]) * Decimal(f["qty"]) for f in order_resp["fills"])
        total_qty = sum(Decimal(f["qty"]) for f in order_resp["fills"])
        if total_qty > 0:
            return (total_price / total_qty).quantize(Decimal("0.00000001"))

    executed_qty = Decimal(order_resp.get("executedQty", "0"))
    quote_qty = Decimal(order_resp.get("cummulativeQuoteQty", "0"))
    
    if executed_qty > 0:
        return (quote_qty / executed_qty).quantize(Decimal("0.00000001"))
    
    raise RuntimeError("Impossible de déterminer le prix moyen d'exécution.")

def place_market_buy(symbol: str, quote_amount: Decimal, qty_amount: Decimal):
    """
    Place un ordre d'achat au marché, en utilisant soit la quantité de base, soit le montant en devise de cotation.
    La logique est simplifiée en combinant les deux modes.
    """
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
    }
    if quote_amount and quote_amount > 0:
        params["quoteOrderQty"] = str(quote_amount)
    elif qty_amount and qty_amount > 0:
        params["quantity"] = str(qty_amount)
    else:
        raise ValueError("Montant d'achat invalide.")
    
    return client.new_order(**params)

def place_oco_sell(symbol: str, qty: Decimal, tp_price: Decimal, sl_price: Decimal):
    """Place un ordre OCO de vente (TP et SL)."""
    tick, step = get_filters(symbol)
    qty = quantize(qty, step)
    tp_price = quantize(tp_price, tick)
    sl_price = quantize(sl_price, tick)
    
    # La stop limit price doit être légèrement inférieure au stop price pour garantir son exécution.
    stop_limit_price = quantize(sl_price * Decimal("0.999"), tick)

    log.info(f"Placement OCO - Qté: {qty}, TP: {tp_price}, SL: {sl_price}")
    
    return client.new_oco_order(
        symbol=symbol,
        side="SELL",
        quantity=str(qty),
        price=str(tp_price),
        stopPrice=str(sl_price),
        stopLimitPrice=str(stop_limit_price),
        stopLimitTimeInForce="GTC"
    )
    
def place_market_sell(symbol: str, qty_amount: Decimal):
    """Place un ordre de vente au marché pour clôturer une position."""
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": str(qty_amount),
    }
    return client.new_order(**params)


# ====================
# Webhook TradingView
# ====================
@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_order_ts
    now = time.time()
    
    if now - _last_order_ts < COOLDOWN_SEC:
        log.warning("Cooldown actif. Ordre ignoré pour éviter le spam d'API.")
        return jsonify({"ok": True, "skipped": "cooldown"}), 200

    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            raise ValueError("Requête JSON invalide.")
        
        log.info(f"Alerte reçue de TradingView: {json.dumps(data, indent=2)}")
        
        signal = data.get("signal", "").upper()
        symbol = data.get("symbol", SYMBOL_DEFAULT).upper()
        
        if not symbol:
            raise ValueError("Symbole manquant dans l'alerte.")

        if signal not in ("BUY", "SELL"):
            log.warning(f"Signal invalide '{signal}' reçu.")
            return jsonify({"ok": False, "error": "Signal invalide."}), 400

        # Récupération des paramètres TP/SL
        tp_pct = Decimal(str(data.get("tp_pct", FALLBACK_TP_PCT)))
        sl_pct = Decimal(str(data.get("sl_pct", FALLBACK_SL_PCT)))

        if signal == "BUY":
            # Détermination de la taille de l'ordre en fonction du mode configuré
            quote_amt = None
            qty_amt = None
            if SIZE_MODE == "fixed_quote":
                quote_amt = FIXED_QUOTE_PER_TRADE
            elif SIZE_MODE == "fixed_qty":
                qty_amt = FIXED_QTY_PER_TRADE
            else:
                raise ValueError("SIZE_MODE invalide. Utiliser 'fixed_quote' ou 'fixed_qty'.")
            
            # 1. Placement de l'ordre d'achat au marché
            buy = place_market_buy(symbol, quote_amt, qty_amt)
            log.info(f"Ordre BUY exécuté: {buy}")
            
            # 2. Récupération des informations de l'ordre
            avg_price = get_avg_fill_price(buy)
            executed_qty = Decimal(buy.get("executedQty", "0"))
            
            if executed_qty == 0:
                log.warning("Ordre d'achat non exécuté immédiatement. Attente d'une seconde...")
                time.sleep(1)
                buy = client.get_order(symbol=symbol, orderId=buy["orderId"])
                executed_qty = Decimal(buy.get("executedQty", "0"))
                if executed_qty == 0:
                    raise RuntimeError("Ordre d'achat non exécuté après une seconde.")
            
            # 3. Placement de l'OCO pour le TP/SL
            tp_price = avg_price * (Decimal(1) + tp_pct/Decimal(100))
            sl_price = avg_price * (Decimal(1) - sl_pct/Decimal(100))
            
            oco = place_oco_sell(symbol, executed_qty, tp_price, sl_price)
            log.info(f"OCO placé (TP/SL): {oco}")
            
            _last_order_ts = now
            
            return jsonify({
                "ok": True,
                "signal": "BUY",
                "avg_price": str(avg_price),
                "executed_qty": str(executed_qty),
                "tp_price": str(tp_price),
                "sl_price": str(sl_price)
            }), 200

        elif signal == "SELL":
            # Logique pour le signal de vente.
            # On cherche à vendre la quantité entière d'une position ouverte.
            log.info("Signal SELL reçu.")
            try:
                # Récupère le solde disponible pour le symbole de base
                balances = client.account()["balances"]
                base_asset = symbol[:-4]
                available_qty = Decimal(next(b for b in balances if b["asset"] == base_asset)["free"])
                
                if available_qty > 0:
                    # Annule tous les ordres en attente (OCO) avant de vendre.
                    client.cancel_open_orders(symbol=symbol)
                    
                    sell = place_market_sell(symbol, available_qty)
                    log.info(f"Ordre SELL exécuté: {sell}")
                    
                    return jsonify({
                        "ok": True,
                        "signal": "SELL",
                        "executed_qty": str(available_qty)
                    }), 200
                else:
                    log.info("Signal SELL reçu, mais pas de solde disponible.")
                    return jsonify({"ok": True, "note": "Pas de solde à vendre."}), 200
            except Exception as e:
                log.error(f"Erreur lors de l'exécution d'un ordre SELL: {e}")
                return jsonify({"ok": False, "error": str(e)}), 500
            
    except ClientError as e:
        log.exception(f"Erreur de l'API Binance: {e.status_code} - {e.error_message}")
        return jsonify({"ok": False, "error": e.error_message}), e.status_code
    except Exception as e:
        log.exception(f"Erreur inattendue: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/")
def root():
    return "Bot TV → Binance Spot est en ligne.", 200
