import os
import json
from flask import Flask, request
import krakenex

app = Flask(__name__)

API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

k = krakenex.API()
k.key = API_KEY
k.secret = API_SECRET

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Received alert:", data)

    signal = data.get("signal")
    symbol = data.get("symbol", "XAUUSD")
    volume = data.get("volume", "0.01")
    ordertype = "market"

    pair = "XAUUSD"

    if signal == "BUY":
        order = k.query_private('AddOrder', {
            'pair': pair,
            'type': 'buy',
            'ordertype': ordertype,
            'volume': volume
        })
    elif signal == "SELL":
        order = k.query_private('AddOrder', {
            'pair': pair,
            'type': 'sell',
            'ordertype': ordertype,
            'volume': volume
        })
    else:
        return {"status": "error", "message": "Invalid signal"}, 400

    return {"status": "success", "order": order}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
