import os
import sys
import logging
import ccxt
from flask import Flask, request, jsonify

# ==============================================================================
# SCRIPT DE BOT DE TRADING POUR PHEMEX AVEC FLASK
# Ce script utilise l'API de Phemex via la bibliothèque CCXT pour le trading
# et reçoit des alertes de TradingView via un webhook.
#
# Auteur : Gemini (avec vos instructions)
# Version : 3.0 (Correction de l'erreur 'unexpected keyword argument')
# ==============================================================================

# ==============================================================================
# 1. CONFIGURATION INITIALE
# ==============================================================================

# Configurez le logging pour voir les messages dans les Logs de Render.
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Initialisation de l'application Flask.
app = Flask(__name__)

# Récupérez vos clés API Phemex depuis les variables d'environnement de Render.
API_KEY = os.environ.get('PHEMEX_API_KEY')
API_SECRET = os.environ.get('PHEMEX_API_SECRET')

# Vérifiez que les clés API sont bien chargées.
if not API_KEY or not API_SECRET:
    logging.error("Erreur : Les variables d'environnement 'PHEMEX_API_KEY' et/ou 'PHEMEX_API_SECRET' ne sont pas définies.")
    sys.exit(1)

# Initialisation du client Phemex via la bibliothèque CCXT.
# Nous ajoutons 'options' pour spécifier le type de compte.
try:
    phemex = ccxt.phemex({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',  # ou 'future', 'spot' selon votre type de trading
        }
    })
    
    # Test de la connexion en récupérant le solde du compte.
    balance = phemex.fetch_balance()
    logging.info("Connexion à l'API Phemex réussie.")
    logging.info(f"Solde du compte : {balance['total']}")
except Exception as e:
    logging.error(f"Une erreur est survenue lors de la connexion à Phemex. Vérifiez vos clés API et les permissions : {e}")
    sys.exit(1)

# ==============================================================================
# 2. ENDPOINTS DU WEBHOOK ET DE SURVEILLANCE
# ==============================================================================

# Nouvelle route pour la surveillance (comme Uptime Robot).
# Elle répond simplement OK pour indiquer que le bot est en vie.
@app.route('/', methods=['GET'])
def index():
    return "OK", 200

# Cette route reçoit les alertes de TradingView.
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Traite les requêtes POST du webhook de TradingView.
    """
    logging.info("Requête Webhook reçue.")
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Payload JSON manquant"}), 400

        logging.info(f"Payload de l'alerte reçu : {data}")

        symbol = data.get('symbol')
        action = data.get('action')
        quantity = data.get('quantity')

        if not all([symbol, action, quantity]):
            return jsonify({"status": "error", "message": "Données d'alerte incomplètes (symbole, action, quantité)"}), 400

        # ======================================================================
        # 3. EXÉCUTION DES ORDRES
        # ======================================================================

        if action == 'BUY':
            logging.info(f"Alerte d'achat reçue. Exécution d'un ordre d'achat pour {symbol} avec une quantité de {quantity}.")
            try:
                # Correction de l'erreur : Utilisation de la fonction create_order
                # pour éviter le problème de mot-clé avec create_market_buy_order.
                order = phemex.create_order(
                    symbol=symbol,
                    type='market',
                    side='buy',
                    amount=quantity
                )
                logging.info(f"Ordre d'achat exécuté avec succès : {order}")
                return jsonify({"status": "success", "message": "Ordre d'achat exécuté"}), 200
            except Exception as e:
                logging.error(f"Erreur lors de l'exécution de l'ordre d'achat: {e}")
                return jsonify({"status": "error", "message": f"Erreur lors de l'exécution de l'ordre d'achat: {str(e)}"}), 500

        elif action == 'SELL':
            logging.info(f"Alerte de vente reçue. Exécution d'un ordre de vente pour {symbol} avec une quantité de {quantity}.")
            try:
                # Correction de l'erreur : Utilisation de la fonction create_order
                # pour éviter le problème de mot-clé avec create_market_sell_order.
                order = phemex.create_order(
                    symbol=symbol,
                    type='market',
                    side='sell',
                    amount=quantity
                )
                logging.info(f"Ordre de vente exécuté avec succès : {order}")
                return jsonify({"status": "success", "message": "Ordre de vente exécuté"}), 200
            except Exception as e:
                logging.error(f"Erreur lors de l'exécution de l'ordre de vente: {e}")
                return jsonify({"status": "error", "message": f"Erreur lors de l'exécution de l'ordre de vente: {str(e)}"}), 500

        else:
            logging.warning(f"Action non reconnue : {action}")
            return jsonify({"status": "error", "message": "Action non reconnue"}), 400

    except Exception as e:
        logging.error(f"Erreur lors du traitement de la requête: {e}")
        return jsonify({"status": "error", "message": f"Erreur lors du traitement de la requête: {str(e)}"}), 500
