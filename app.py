@app.post("/webhook")
def webhook():
    # (secret inchangé – laisse vide si tu n'en veux pas)
    if WEBHOOK_SECRET:
        recv = request.headers.get("X-Webhook-Secret", "")
        if recv != WEBHOOK_SECRET:
            app.logger.warning("Webhook rejeté: mauvais secret")
            return jsonify({"status": "forbidden"}), 403

    data = request.get_json(force=True, silent=True) or {}
    signal = (data.get("signal") or "").upper()        # "BUY" | "SELL"
    symbol = data.get("symbol") or "KRAKEN:BTCEUR"
    timeframe = str(data.get("timeframe", ""))
    price_in = float(str(data.get("price", "0")).replace(",", ".") or 0)

    if signal not in ("BUY","SELL"):
        return jsonify({"status":"ignored","msg":"signal manquant/invalid"}), 200

    pair = map_symbol_to_pair(symbol)

    # Prix: payload sinon ticker
    try:
        price = price_in if price_in > 0 else get_ticker_price(pair)
    except Exception as e:
        app.logger.exception(f"Erreur prix: {e}")
        return jsonify({"status":"error","msg":str(e)}), 500

    # Sizing demandé
    try:
        qty_wanted = compute_order_qty(price)
    except Exception as e:
        app.logger.exception(f"Erreur sizing: {e}")
        return jsonify({"status":"error","msg":str(e)}), 500

    # Construction d’ordre de base
    order = {
        "pair": pair,
        "ordertype": "market" if ORDER_TYPE=="market" else "limit",
    }

    # BUY : on utilise la qty calculée
    if signal == "BUY":
        order["type"] = "buy"
        order["volume"] = f"{qty_wanted:.8f}"
        if ORDER_TYPE == "limit":
            delta = max(price * 0.0002, 1.0)  # ~0,02%
            limit_price = price - delta
            order["price"] = f"{limit_price:.1f}"
            if POST_ONLY: order["oflags"] = "post"
        app.logger.info(f"ORDER BUY {order['volume']} {pair} @ {order['ordertype']} ~{price:.1f} | TF {timeframe}")

    # SELL : on limite à ce que tu possèdes en BTC
    if signal == "SELL":
        try:
            bal = get_balances()
            btc_avail = float(bal.get("BTC", 0.0))
        except Exception as e:
            app.logger.exception(f"Erreur balance: {e}")
            btc_avail = 0.0

        qty_sell = min(qty_wanted, truncate_qty(btc_avail, 8))
        if qty_sell <= 0:
            app.logger.warning(f"SELL ignoré: solde BTC insuffisant (dispo={btc_avail})")
            return jsonify({"status":"skipped","msg":"no_btc_available"}), 200

        order["type"] = "sell"
        order["volume"] = f"{qty_sell:.8f}"
        if ORDER_TYPE == "limit":
            delta = max(price * 0.0002, 1.0)
            limit_price = price + delta
            order["price"] = f"{limit_price:.1f}"
            if POST_ONLY: order["oflags"] = "post"
        app.logger.info(f"ORDER SELL {order['volume']} {pair} @ {order['ordertype']} ~{price:.1f} | TF {timeframe}")

    # Envoi Kraken
    try:
        resp = kraken_private("/0/private/AddOrder", order)
        app.logger.info(f"Kraken: {resp}")
        status = "sent" if not resp.get("error") else "kraken_error"
        return jsonify({"status":status, "order":resp}), 200
    except requests.HTTPError as e:
        app.logger.exception(f"HTTPError Kraken: {e}")
        return jsonify({"status":"error","msg":str(e)}), 502
    except Exception as e:
        app.logger.exception(f"Exception Kraken: {e}")
        return jsonify({"status":"error","msg":str(e)}), 500
