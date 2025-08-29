import os
import time
import math
import logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
from binance.spot import Spot as Binance
from binance.error import ClientError

# ====================
# Configuration
# ====================
app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("tv-binance-bot")

# Variables d'environnement
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
API_PASSPHRASE = os.getenv("BINANCE_API_PASSPHRASE", "") # Nouvelle variable pour la passphrase
SYMBOL_DEFAULT = os.getenv("SYMBOL", "BTCUSDT").upper()
SIZE_MODE = os.getenv("SIZE_MODE", "fixed_quote").lower()
FIXED_QUOTE_PER_TRADE = Decimal(os.getenv("FIXED_QUOTE_PER_TRADE", "20"))
FIXED_QTY_PER_TRADE = Decimal(os.getenv("FIXED_QTY_PER_TRADE", "0.001"))
FALLBACK_TP_PCT = Decimal(os.getenv("FALLBACK_TP_PCT", "0.8"))
FALLBACK_SL_PCT = Decimal(os.getenv("FALLBACK_SL_PCT", "0.5"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "5"))
_last_order_ts = 0

# Initialisation du client Binance avec la passphrase
client = Binance(api_key=API_KEY, api_secret=API_SECRET, passphrase=API_PASSPHRASE)

# ====================
# Fonctions utilitaires
# ====================
def get_filters(symbol: str):
    """Récupère les filtres d'échange pour un symbole."""
    try:
        info = client.exchange_info(symbol=symbol)
        filters = info["symbols"][0]["filters"]
        price_filter = next(x for x in filters if x["filterType"] == "PRICE_FILTER")
        lot_filter = next(x for x in filters if x["filterType"] == "LOT_SIZE")
        notion_filter = next(x for x in filters if x["filterType"] == "NOTIONAL" or x["filterType"] == "MIN_NOTIONAL")
        
        return Decimal(price_filter["tickSize"]), Decimal(lot_filter["stepSize"]), Decimal(notion_filter.get("minNotional", "0"))
    except Exception as e:
        log.error(f"Erreur lors de la récupération des filtres pour le symbole {symbol}: {e}")
        raise

def quantize(value: Decimal, step: Decimal) -> Decimal:
    """Coupe une valeur à l’incrément de pas le plus proche."""
    if step == 0:
        return value
    precision = max(0, -step.as_tuple().exponent)
    return (value // step) * step if step > 0 else value.quantize(Decimal(10) ** -precision, rounding=ROUND_DOWN)

def get_avg_fill_price(order_resp) -> Decimal:
    """Calcule le prix moyen d'exécution d'un ordre."""
    executed_qty = Decimal(order_resp.get("executedQty", "0"))
    quote_qty = Decimal(order_resp.get("cummulativeQuoteQty", "0"))
    
    if executed_qty > 0:
        return (quote_qty / executed_qty).quantize(Decimal("0.00000001"))
    
    fills = order_resp.get("fills", [])
    if fills:
        total_price = sum(Decimal(f["price"]) * Decimal(f["qty"]) for f in fills)
        total_qty = sum(Decimal(f["qty"]) for f in fills)
        if total_qty > 0:
            return (total_price / total_qty).quantize(Decimal("0.00000001"))
    
    raise RuntimeError("Impossible de déterminer le prix moyen d'exécution.")

def place_market_buy(symbol: str, quote_amount: Decimal|None, qty_amount: Decimal|None):
    """Place un ordre d'achat au marché."""
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
    tick, step, _ = get_filters(symbol)
    qty = quantize(qty, step)
    tp_price = quantize(tp_price, tick)
    sl_price = quantize(sl_price, tick)

    stop_limit_price = quantize(sl_price * Decimal("0.999"), tick)

    return client.new_oco_order(
        symbol=symbol,
        side="SELL",
        quantity=str(qty),
        price=str(tp_price),
        stopPrice=str(sl_price),
        stopLimitPrice=str(stop_limit_price),
        stopLimitTimeInForce="GTC"
    )

# ====================
# Webhook TradingView
# ====================
@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_order_ts
    now = time.time()
    
    if now - _last_order_ts < COOLDOWN_SEC:
        log.info("Cooldown actif. Ordre ignoré.")
        return jsonify({"ok": True, "skipped": "cooldown"}), 200

    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            raise ValueError("Requête JSON invalide.")
        
        log.info(f"Alerte reçue de TradingView: {data}")
        
        signal = data.get("signal", "").upper()
        symbol = data.get("symbol", SYMBOL_DEFAULT).upper()
        
        if not symbol:
            raise ValueError("Symbole manquant dans l'alerte.")

        # Paramètres TP/SL reçus ou fallback
        tp_pct = Decimal(str(data.get("tp_pct", FALLBACK_TP_PCT)))
        sl_pct = Decimal(str(data.get("sl_pct", FALLBACK_SL_PCT)))

        if signal not in ("BUY", "SELL"):
            return jsonify({"ok": False, "error": "Signal invalide."}), 400

        if signal == "SELL":
            log.info("Signal SELL reçu. Ordre ignoré car Spot ne supporte pas le short.")
            return jsonify({"ok": True, "note": "SELL ignoré."}), 200

        # --- Achat au marché ---
        if SIZE_MODE == "fixed_quote":
            quote_amt = FIXED_QUOTE_PER_TRADE
            qty_amt = None
        elif SIZE_MODE == "fixed_qty":
            qty_amt = FIXED_QTY_PER_TRADE
            quote_amt = None
        else:
            raise ValueError("SIZE_MODE invalide. Utiliser 'fixed_quote' ou 'fixed_qty'.")
        
        buy = place_market_buy(symbol, quote_amt, qty_amt)
        log.info(f"Ordre BUY exécuté: {buy}")

        # Prix moyen et quantité exécutée
        avg = get_avg_fill_price(buy)
        executed_qty = Decimal(buy.get("executedQty", "0"))
        
        if executed_qty == 0:
            time.sleep(1)
            buy = client.get_order(symbol=symbol, orderId=buy["orderId"])
            executed_qty = Decimal(buy.get("executedQty", "0"))
            if executed_qty == 0:
                raise RuntimeError("Ordre d'achat non exécuté.")
        
        # --- Placement de l'OCO (TP/SL) ---
        tp_price = avg * (Decimal(1) + tp_pct/Decimal(100))
        sl_price = avg * (Decimal(1) - sl_pct/Decimal(100))
        
        oco = place_oco_sell(symbol, executed_qty, tp_price, sl_price)
        log.info(f"OCO placé (TP/SL): {oco}")

        _last_order_ts = now

        return jsonify({
            "ok": True,
            "avg_price": str(avg),
            "executed_qty": str(executed_qty),
            "tp_price": str(tp_price),
            "sl_price": str(sl_price)
        }), 200

    except ClientError as e:
        log.exception(f"Erreur de l'API Binance: {e.status_code} - {e.error_message}")
        return jsonify({"ok": False, "error": e.error_message}), e.status_code
    except Exception as e:
        log.exception(f"Erreur inattendue: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/")
def root():
    return "Bot TV → Binance Spot est en ligne.", 200

