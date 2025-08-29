import os, time, math, logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# --- Binance (SDK officiel moderne) ---
# pip install binance-connector
from binance.spot import Spot as Binance

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("tv-binance-bot")

# =========
# ENV VARS
# =========
API_KEY             = os.getenv("BINANCE_API_KEY", "")
API_SECRET          = os.getenv("BINANCE_API_SECRET", "")
SYMBOL_DEFAULT      = os.getenv("SYMBOL", "BTCUSDT").upper()          # ex: BTCUSDT
ORDER_TYPE          = os.getenv("ORDER_TYPE", "market").lower()      # market uniquement ici
SIZE_MODE           = os.getenv("SIZE_MODE", "fixed_quote").lower()  # fixed_quote | fixed_qty
FIXED_QUOTE_PER_TRADE = Decimal(os.getenv("FIXED_QUOTE_PER_TRADE", "20"))  # en USDT
FIXED_QTY_PER_TRADE   = Decimal(os.getenv("FIXED_QTY_PER_TRADE", "0.001")) # en BASE

# Fallback TP/SL si TradingView n’envoie rien
FALLBACK_TP_PCT     = Decimal(os.getenv("FALLBACK_TP_PCT", "0.8"))   # %
FALLBACK_SL_PCT     = Decimal(os.getenv("FALLBACK_SL_PCT", "0.5"))   # %

# Sécurité / anti-spam
COOLDOWN_SEC        = int(os.getenv("COOLDOWN_SEC", "5"))
_last_order_ts      = 0

# Client Binance
client = Binance(api_key=API_KEY, api_secret=API_SECRET)

# =========
# Helpers
# =========
def get_filters(symbol: str):
    """Récupère tickSize, stepSize, minNotional pour arrondir correctement."""
    info = client.exchange_info(symbol=symbol)
    f = info["symbols"][0]["filters"]
    price_filter = next(x for x in f if x["filterType"] == "PRICE_FILTER")
    lot_filter   = next(x for x in f if x["filterType"] == "LOT_SIZE")
    notion_filter= next(x for x in f if x["filterType"] == "NOTIONAL" or x["filterType"]=="MIN_NOTIONAL")
    return Decimal(price_filter["tickSize"]), Decimal(lot_filter["stepSize"]), Decimal(notion_filter.get("minNotional","0"))

def quantize(value: Decimal, step: Decimal) -> Decimal:
    """Coupe à l’incrément autorisé (pas d’arrondi vers le haut)."""
    if step == 0:
        return value
    precision = max(0, -step.as_tuple().exponent)
    return (value // step) * step if step > 0 else value.quantize(Decimal(10) ** -precision, rounding=ROUND_DOWN)

def get_avg_fill_price(order_resp) -> Decimal:
    """Calcule le prix moyen exécuté à partir de la réponse Binance MARKET."""
    # new_order_resp_type par défaut = RESULT => 'fills' peut être vide; on récupère cummulativeQuoteQty/executedQty
    executed_qty = Decimal(order_resp.get("executedQty", "0"))
    quote_qty    = Decimal(order_resp.get("cummulativeQuoteQty", "0"))
    if executed_qty > 0:
        return (quote_qty / executed_qty).quantize(Decimal("0.00000001"))
    # fallback (rare)
    fills = order_resp.get("fills", [])
    if fills:
        total = sum(Decimal(f["price"]) * Decimal(f["qty"]) for f in fills)
        qty   = sum(Decimal(f["qty"]) for f in fills)
        return (total/qty).quantize(Decimal("0.00000001"))
    raise RuntimeError("Impossible de déterminer le prix moyen exécuté.")

def place_market_buy(symbol: str, quote_amount: Decimal|None, qty_amount: Decimal|None):
    """Place un BUY au marché. On privilégie quoteOrderQty (montant en USDT)."""
    if quote_amount and quote_amount > 0:
        resp = client.new_order(
            symbol=symbol, side="BUY", type="MARKET",
            quoteOrderQty=str(quote_amount)  # Binance arrondit la qty selon LOT_SIZE
        )
    else:
        resp = client.new_order(
            symbol=symbol, side="BUY", type="MARKET",
            quantity=str(qty_amount)
        )
    return resp

def place_oco_sell(symbol: str, qty: Decimal, tp_price: Decimal, sl_price: Decimal):
    """Place un OCO SELL (TP limit + SL stop-limit)."""
    tick, step, _ = get_filters(symbol)
    qty      = quantize(qty, step)
    tp_price = quantize(tp_price, tick)
    sl_price = quantize(sl_price, tick)

    # Sur Spot, l’OCO nécessite stopPrice + stopLimitPrice (léger décalage recommandé)
    stop_limit_price = quantize(sl_price * Decimal("0.999"), tick)

    resp = client.new_oco_order(
        symbol=symbol, side="SELL", quantity=str(qty),
        price=str(tp_price),
        stopPrice=str(sl_price),
        stopLimitPrice=str(stop_limit_price),
        stopLimitTimeInForce="GTC"
    )
    return resp

# ===================
# Webhook TradingView
# ===================
@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_order_ts
    now = time.time()
    if now - _last_order_ts < COOLDOWN_SEC:
        return jsonify({"ok": True, "skipped": "cooldown"}), 200

    data = request.get_json(force=True, silent=True) or {}
    log.info(f"Alerte reçue: {data}")

    signal = str(data.get("signal", "")).upper()           # BUY / SELL
    symbol = str(data.get("symbol", SYMBOL_DEFAULT)).upper()

    # Paramètres TP/SL reçus ou fallback
    tp_pct = Decimal(str(data.get("tp_pct", FALLBACK_TP_PCT)))
    sl_pct = Decimal(str(data.get("sl_pct", FALLBACK_SL_PCT)))

    if signal not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "signal invalide"}), 400

    # BUY = ouvre un long sur Spot (achat de l’actif)
    # SELL = sur Spot on ne shorte pas; ici on interprète SELL comme “fermer/stopper” une position existante → on ne l’automatise pas (optionnel)
    if signal == "SELL":
        log.info("Signal SELL reçu (Spot ne shorte pas). Aucun ordre marché automatique envoyé.")
        return jsonify({"ok": True, "note": "SELL ignoré sur Spot (pas de short)."}), 200

    try:
        tick, step, min_notional = get_filters(symbol)

        # --- Taille de l’ordre ---
        market_price = Decimal(client.ticker_price(symbol)["price"])
        if SIZE_MODE == "fixed_quote":
            quote_amt = FIXED_QUOTE_PER_TRADE
            if quote_amt < min_notional:
                raise ValueError(f"FIXED_QUOTE_PER_TRADE {quote_amt} < minNotional {min_notional}")
            qty_amt = None
        elif SIZE_MODE == "fixed_qty":
            qty_amt = quantize(FIXED_QTY_PER_TRADE, step)
            quote_amt = None
            if (qty_amt * market_price) < min_notional:
                raise ValueError("fixed_qty trop petit pour le minNotional de Binance.")
        else:
            raise ValueError("SIZE_MODE invalide. Utilise 'fixed_quote' ou 'fixed_qty'.")

        # --- Achat marché ---
        buy = place_market_buy(symbol, quote_amt, qty_amt)
        _last_order_ts = now
        log.info(f"Ordre BUY exécuté: {buy}")

        # Prix moyen et qty exécutée
        avg = get_avg_fill_price(buy)
        executed_qty = Decimal(buy.get("executedQty", "0"))
        if executed_qty == 0:
            # si on a utilisé quoteOrderQty, récupérer l’asset achetée via compte
            time.sleep(0.5)
            executed_qty = Decimal(client.get_order(symbol=symbol, orderId=buy["orderId"]).get("executedQty", "0"))

        # --- TP / SL en OCO (SELL) ---
        tp_price = avg * (Decimal(1) + tp_pct/Decimal(100))
        sl_price = avg * (Decimal(1) - sl_pct/Decimal(100))
        oco = place_oco_sell(symbol, executed_qty, tp_price, sl_price)
        log.info(f"OCO placé (TP/SL): {oco}")

        return jsonify({
            "ok": True,
            "avg": str(avg),
            "executedQty": str(executed_qty),
            "tp_pct": str(tp_pct),
            "sl_pct": str(sl_pct),
            "tp_price": str(tp_price),
            "sl_price": str(sl_price)
        }), 200

    except Exception as e:
        log.exception("Erreur traitement webhook")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/")
def root():
    return "TV → Binance Spot bot : OK", 200
