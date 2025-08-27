# Fichier : app.py
from flask import Flask, request, jsonify
import os
import requests
import json
import hmac
import hashlib
import base64
import time

app = Flask(__name__)

# ==============================
# 0) VARIABLES D'ENVIRONNEMENT (À CONFIGURER SUR RENDER)
# ==============================
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_API_SECRET = os.getenv("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.getenv("OKX_API_PASSPHRASE")

BASE = os.getenv("BASE", "BTC")
QUOTE = os.getenv("QUOTE", "USDT")
ORDER_TYPE = os.getenv("ORDER_TYPE", "market")
SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur")
FIXED_EUR_PER_TRADE = float(os.getenv("FIXED_EUR_PER_TRADE", 50.0))
FALLBACK_SL_PCT = float(os.getenv("FALLBACK_SL_PCT", 1.0))
FALLBACK_TP_PCT = float(os.getenv("FALLBACK_TP_PCT", 0.6))
TRAIL_START_PCT = float(os.getenv("TRAIL_START_PCT", 0.6))
TRAIL_STEP_PCT = float(os.getenv("TRAIL_STEP_PCT", 0.3))

# ==============================
# 1) FONCTION D'APPEL À L'API OKX
# ==============================
def place_okx_order(symbol, side, price, sz):
    """
    Passe un ordre de trading sur OKX en utilisant leur API.
    """
    url = "https://www.okx.com/api/v5/trade/order"
    # Utilisation d'un timestamp en millisecondes pour éviter les problèmes de signature
    timestamp = str(int(time.time() * 1000))
    
    # Construction du corps de la requête
    body = {
        "instId": f"{BASE}-{QUOTE}",
        "tdMode": "cash",  # Utilisation du mode cash
        "side": side,
        "ordType": ORDER_TYPE,
        "sz": str(sz),
        "ccy": QUOTE # Spécifie la devise de référence
    }
    
    # Création de la chaîne de signature
    # Le 'request_path' est l'URL après le domaine
    prehash = timestamp + "POST" + url.replace("https://www.okx.com", "") + json.dumps(body)
    
    signature = base64.b64encode(
        hmac.new(
            OKX_API_SECRET.encode('utf-8'),
            prehash.encode('utf-8'),
            hashlib.sha256
        ).digest()
    ).decode('utf-8')
    
    # Définition des headers
    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

    try:
        print(f"Tentative de placement d'ordre : {body}")
        response = requests.post(url, headers=headers, data=json.dumps(body))
        response.raise_for_status()  # Lève une exception pour les codes d'erreur HTTP (4xx ou 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erreur lors de l'appel à l'API OKX: {e}")
        return {"code": -1, "msg": str(e)}

# ==============================
# 2) ENDPOINT POUR LES ALERTES TRADINGVIEW
# ==============================
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Reçoit les alertes JSON de TradingView et passe des ordres sur OKX.
    """
    try:
        data = request.json
        print(f"Alerte reçue de TradingView : {data}")

        # Vérification basique de la validité des données
        if not data or 'signal' not in data:
            return jsonify({"status": "error", "message": "Données JSON invalides"}), 400

        signal = data.get('signal')
        symbol = data.get('symbol')
        price_str = data.get('price')

        if not all([signal, symbol, price_str]):
            print("Données manquantes dans le payload.")
            return jsonify({"status": "error", "message": "Données de signal manquantes"}), 400

        try:
            price = float(price_str)
        except ValueError:
            print(f"Erreur de conversion de prix : {price_str}")
            return jsonify({"status": "error", "message": "Erreur de format de prix"}), 400
        
        # Calcul de la taille de l'ordre en fonction du mode (pour l'instant, seulement FIXED_EUR)
        order_size = FIXED_EUR_PER_TRADE / price

        # Logique de trading
        if signal == 'BUY':
            print(f"Signal d'achat reçu pour {symbol}. Taille de l'ordre : {order_size:.4f}")
            order_result = place_okx_order(symbol=symbol, side="buy", price=price, sz=order_size)
            print(f"Résultat de l'ordre OKX (BUY) : {order_result}")
            return jsonify({"status": "success", "order": order_result})

        elif signal == 'SELL':
            print(f"Signal de vente reçu pour {symbol}. Taille de l'ordre : {order_size:.4f}")
            order_result = place_okx_order(symbol=symbol, side="sell", price=price, sz=order_size)
            print(f"Résultat de l'ordre OKX (SELL) : {order_result}")
            return jsonify({"status": "success", "order": order_result})

        else:
            print(f"Signal non reconnu: {signal}")
            return jsonify({"status": "success", "message": "Signal non pris en charge"}), 200

    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")
        return jsonify({"status": "error", "message": f"Erreur du serveur : {e}"}), 500

if __name__ == '__main__':
    # Le port 10000 est requis par Render pour les services Web
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
