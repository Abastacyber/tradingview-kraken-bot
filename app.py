# app.py

import os
import json
import logging
from flask import Flask, request, jsonify
from binance.client import Client # L'importation correcte pour python-binance

# Configurez le logging pour voir les messages dans les logs de Render
logging.basicConfig(level=logging.INFO)

# Initialisation de l'application Flask
app = Flask(__name__)

# Récupérez vos clés API depuis les variables d'environnement de Render
# Assurez-vous d'avoir configuré API_KEY et API_SECRET dans les "Environment" de votre service Render
API_KEY = os.environ.get('BINANCE_API_KEY')
API_SECRET = os.environ.get('BINANCE_API_SECRET')

# Vérifiez que les clés API sont bien chargées
if not API_KEY or not API_SECRET:
    logging.error("Erreur: Les variables d'environnement BINANCE_API_KEY et/ou BINANCE_API_SECRET ne sont pas définies.")
    # Renvoie une erreur pour éviter d'initialiser le client sans clés
    exit(1)

# Initialisation du client Binance (la ligne que vous ne trouviez pas)
# C'est ici que l'objet client est créé pour interagir avec l'API de Binance
client = Client(API_KEY, API_SECRET)

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Ce point de terminaison reçoit les alertes de TradingView.
    """
    # Lisez le payload JSON de l'alerte
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Payload JSON manquant"}), 400
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "Format JSON invalide"}), 400

    logging.info(f"Alerte reçue : {data}")

    # Vérifiez si le message contient une information de "type" pour l'ordre
    action = data.get('action')
    symbol = data.get('symbol')
    price = data.get('price')
    quantity = data.get('quantity')

    if not all([action, symbol, price, quantity]):
        return jsonify({"status": "error", "message": "Données d'alerte incomplètes"}), 400

    # Logique pour exécuter l'ordre
    if action == 'BUY':
        try:
            # L'appel de fonction correct pour créer un ordre d'achat
            order = client.create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity
            )
            logging.info(f"Ordre d'achat exécuté : {order}")
            return jsonify({"status": "success", "message": "Ordre d'achat exécuté"}), 200
        except Exception as e:
            logging.error(f"Erreur lors de l'exécution de l'ordre d'achat : {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    elif action == 'SELL':
        try:
            # L'appel de fonction correct pour créer un ordre de vente
            order = client.create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )
            logging.info(f"Ordre de vente exécuté : {order}")
            return jsonify({"status": "success", "message": "Ordre de vente exécuté"}), 200
        except Exception as e:
            logging.error(f"Erreur lors de l'exécution de l'ordre de vente : {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    else:
        return jsonify({"status": "error", "message": "Action non reconnue"}), 400

# Le "gunicorn" de Render a besoin de cette ligne pour lancer le serveur
if __name__ == '__main__':
    app.run()
