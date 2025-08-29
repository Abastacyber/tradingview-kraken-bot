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
# Version : 1.0
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
# Assurez-vous d'avoir configuré PHEMEX_API_KEY et PHEMEX_API_SECRET.
API_KEY = os.environ.get('PHEMEX_API_KEY')
API_SECRET = os.environ.get('PHEMEX_API_SECRET')

# Vérifiez que les clés API sont bien chargées.
if not API_KEY or not API_SECRET:
    logging.error("Erreur : Les variables d'environnement 'PHEMEX_API_KEY' et/ou 'PHEMEX_API_SECRET' ne sont pas définies.")
    sys.exit(1)

# Initialisation du client Phemex via la bibliothèque CCXT.
try:
    phemex = ccxt.phemex({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True, # Important pour éviter les erreurs d'API
    })
    logging.info("Client Phemex initialisé avec succès.")
except Exception as e:
    logging.error(f"Une erreur inattendue est survenue lors de l'initialisation de l'API Phemex : {e}")
    sys.exit(1)

# ==============================================================================
# 2. ENDPOINT DU WEBHOOK
# ==============================================================================

# Ce point de terminaison reçoit les alertes de TradingView.
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Traite les requêtes POST du webhook de TradingView.
    """
    logging.info("Requête Webhook reçue.")
    try:
        # Lisez le payload JSON de l'alerte.
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Payload JSON manquant"}), 400

        logging.info(f"Payload de l'alerte reçu : {data}")

        # Vérifiez que le message contient les informations nécessaires.
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
                # Appelle la fonction CCXT pour créer un ordre d'achat au marché.
                # L'API Phemex utilise un format de symbole spécifique, par exemple "BTC/USDT:USDT"
                # pour les contrats perpétuels. Vous devrez peut-être ajuster cela en fonction
                # de votre marché (spot, perpétuel, etc.).
                order = phemex.create_market_buy_order(
                    symbol=symbol,
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
                # Appelle la fonction CCXT pour créer un ordre de vente au marché.
                order = phemex.create_market_sell_order(
                    symbol=symbol,
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
        # Gère les erreurs de format JSON ou d'autres erreurs inattendues.
        logging.error(f"Erreur lors du traitement de la requête: {e}")
        return jsonify({"status": "error", "message": f"Erreur lors du traitement de la requête: {str(e)}"}), 500
import os
import sys
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from flask import Flask, request, jsonify

# ==============================================================================
# SCRIPT DE BOT DE TRADING POUR BINANCE AVEC FLASK
# Ce script utilise l'API de Binance pour le trading et reçoit des alertes
# de TradingView via un webhook.
#
# Auteur : Gemini (avec vos instructions)
# Version : 1.0
# ==============================================================================

# ==============================================================================
# 1. CONFIGURATION INITIALE
# ==============================================================================

# Configurez le logging pour voir les messages dans les Logs de Render.
# Utilisez logging.INFO pour les messages d'information standard.
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Initialisation de l'application Flask.
# Le nom de l'application est 'app'.
app = Flask(__name__)

# Récupérez vos clés API depuis les variables d'environnement de Render.
# Assurez-vous d'avoir configuré BINANCE_API_KEY et BINANCE_API_SECRET dans
# la section 'Environment' de votre service Render.
API_KEY = os.environ.get('BINANCE_API_KEY')
API_SECRET = os.environ.get('BINANCE_API_SECRET')

# Vérifiez que les clés API sont bien chargées.
if not API_KEY or not API_SECRET:
    logging.error("Erreur : Les variables d'environnement 'BINANCE_API_KEY' et/ou 'BINANCE_API_SECRET' ne sont pas définies.")
    # Renvoie une erreur pour empêcher l'initialisation du client sans clés.
    sys.exit(1)

# Initialisation du client Binance.
# C'est l'objet client qui est créé pour interagir avec l'API de Binance.
try:
    client = Client(API_KEY, API_SECRET)
    logging.info("Client Binance initialisé avec succès. Connexion API vérifiée.")
except BinanceAPIException as e:
    logging.error(f"Échec de la connexion à l'API de Binance: {e.message}")
    sys.exit(1)
except Exception as e:
    logging.error(f"Une erreur inattendue est survenue lors de la connexion API: {e}")
    sys.exit(1)

# ==============================================================================
# 2. ENDPOINT DU WEBHOOK
# ==============================================================================

# Ce point de terminaison reçoit les alertes de TradingView.
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Traite les requêtes POST du webhook de TradingView.
    """
    logging.info("Requête Webhook reçue.")
    try:
        # Lisez le payload JSON de l'alerte.
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Payload JSON manquant"}), 400

        logging.info(f"Payload de l'alerte reçu : {data}")

        # Vérifiez que le message contient les informations nécessaires.
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
                # Appelle la fonction correcte pour créer un ordre d'achat.
                order = client.create_order(
                    symbol=symbol,
                    side='BUY',
                    type='MARKET',
                    quantity=quantity
                )
                logging.info(f"Ordre d'achat exécuté avec succès : {order}")
                return jsonify({"status": "success", "message": "Ordre d'achat exécuté"}), 200
            except BinanceAPIException as e:
                logging.error(f"Erreur lors de l'exécution de l'ordre d'achat: {e.message}")
                return jsonify({"status": "error", "message": f"Erreur lors de l'exécution de l'ordre d'achat: {str(e)}"}), 500
            except Exception as e:
                logging.error(f"Erreur inattendue lors de l'achat: {e}")
                return jsonify({"status": "error", "message": f"Erreur inattendue lors de l'achat: {str(e)}"}), 500

        elif action == 'SELL':
            logging.info(f"Alerte de vente reçue. Exécution d'un ordre de vente pour {symbol} avec une quantité de {quantity}.")
            try:
                # Appelle la fonction correcte pour créer un ordre de vente.
                order = client.create_order(
                    symbol=symbol,
                    side='SELL',
                    type='MARKET',
                    quantity=quantity
                )
                logging.info(f"Ordre de vente exécuté avec succès : {order}")
                return jsonify({"status": "success", "message": "Ordre de vente exécuté"}), 200
            except BinanceAPIException as e:
                logging.error(f"Erreur lors de l'exécution de l'ordre de vente: {e.message}")
                return jsonify({"status": "error", "message": f"Erreur lors de l'exécution de l'ordre de vente: {str(e)}"}), 500
            except Exception as e:
                logging.error(f"Erreur inattendue lors de la vente: {e}")
                return jsonify({"status": "error", "message": f"Erreur inattendue lors de la vente: {str(e)}"}), 500

        else:
            logging.warning(f"Action non reconnue : {action}")
            return jsonify({"status": "error", "message": "Action non reconnue"}), 400

    except Exception as e:
        # Gère les erreurs de format JSON ou d'autres erreurs inattendues.
        logging.error(f"Erreur lors du traitement de la requête: {e}")
        return jsonify({"status": "error", "message": f"Erreur lors du traitement de la requête: {str(e)}"}), 500

