from flask import Flask, request, jsonify
import logging
from phemex_api import PhemexAPI  # Assurez-vous que cette classe existe et est configurée

app = Flask(__name__)

# Configurez le logging pour un meilleur débogage
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Remplacez les valeurs ci-dessous par vos vraies clés
phemex_api_key = "VOTRE_CLE_API_PHEMEX"
phemex_api_secret = "VOTRE_SECRET_API_PHEMEX"
phemex_client = PhemexAPI(phemex_api_key, phemex_api_secret)

@app.route('/webhook', methods=['POST'])
def webhook_receiver():
    try:
        data = request.json
        if not data:
            logging.error("Données de webhook manquantes.")
            return jsonify({"status": "error", "message": "Données manquantes"}), 400

        logging.info(f"Webhook reçu: {data}")

        # Extrait l'action de TradingView
        action = data.get('action')
        symbol = data.get('symbol')
        price = data.get('price')
        volume = data.get('volume', 0.01)  # Définir un volume par défaut

        if not action or not symbol or not price:
            logging.error("Action, symbole ou prix manquant dans le payload.")
            return jsonify({"status": "error", "message": "Payload incomplet"}), 400

        # S'assurer que les valeurs sont numériques
        try:
            volume = float(volume)
            price = float(price)
        except (ValueError, TypeError):
            logging.error(f"Erreur de conversion des données: volume={volume}, price={price}")
            return jsonify({"status": "error", "message": "Volume ou prix invalide"}), 400

        if action == "buy":
            logging.info(f"Alerte d'achat reçue pour {symbol} à {price} avec un volume de {volume}.")
            # Log de l'API Phemex avant l'appel
            logging.info(f"Tentative de passage d'ordre d'achat sur Phemex pour {symbol}...")
            # Remplacez "YOUR_PRODUCT_TYPE" par "linear", "spot", ou "contract"
            order_result = phemex_client.create_order(symbol, 'buy', volume, price, 'limit', 'YOUR_PRODUCT_TYPE')
            logging.info(f"Résultat de l'ordre d'achat: {order_result}")

            if order_result and order_result.get('code') == 0:
                logging.info(f"Ordre d'achat passé avec succès pour {symbol}!")
                return jsonify({"status": "success", "message": "Ordre d'achat passé"}), 200
            else:
                logging.error(f"Échec du passage de l'ordre d'achat pour {symbol}. Réponse de l'API: {order_result}")
                return jsonify({"status": "error", "message": "Échec de l'ordre d'achat"}), 500

        elif action == "sell":
            logging.info(f"Alerte de vente reçue pour {symbol} à {price} avec un volume de {volume}.")
            logging.info(f"Tentative de passage d'ordre de vente sur Phemex pour {symbol}...")
            # Remplacez "YOUR_PRODUCT_TYPE" par "linear", "spot", ou "contract"
            order_result = phemex_client.create_order(symbol, 'sell', volume, price, 'limit', 'YOUR_PRODUCT_TYPE')
            logging.info(f"Résultat de l'ordre de vente: {order_result}")

            if order_result and order_result.get('code') == 0:
                logging.info(f"Ordre de vente passé avec succès pour {symbol}!")
                return jsonify({"status": "success", "message": "Ordre de vente passé"}), 200
            else:
                logging.error(f"Échec du passage de l'ordre de vente pour {symbol}. Réponse de l'API: {order_result}")
                return jsonify({"status": "error", "message": "Échec de l'ordre de vente"}), 500

        else:
            logging.warning(f"Action non reconnue: {action}")
            return jsonify({"status": "warning", "message": "Action non reconnue"}), 400

    except Exception as e:
        logging.critical(f"Erreur fatale dans le webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Erreur interne"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
