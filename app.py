import requests
import hmac
import hashlib
import time
import json
import uuid

class PhemexAPI:
    def __init__(self, api_key, api_secret, base_url="https://api.phemex.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
    
    def _generate_signature(self, path, query_string):
        """
        Génère une signature HMAC-SHA256 pour l'authentification de la requête.
        """
        message = path + query_string
        signature = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return signature

    def create_order(self, symbol, side, volume, price, order_type, product_type):
        """
        Crée un nouvel ordre sur Phemex.
        
        Paramètres:
        - symbol (str): La paire de trading (ex: "BTCUSDT")
        - side (str): "buy" ou "sell"
        - volume (float): La quantité à trader
        - price (float): Le prix de l'ordre
        - order_type (str): "limit", "market", etc.
        - product_type (str): "linear" ou "spot"
        
        Retourne la réponse de l'API de Phemex.
        """
        path = "/v1/api-trade/orders" # Remplacez par le bon chemin si nécessaire pour votre type d'ordre
        client_order_id = str(uuid.uuid4())
        
        request_body = {
            "symbol": symbol,
            "side": side,
            "volume": int(volume * 1e8),  # Phemex attend des valeurs entières pour le volume (basé sur 1e8)
            "price": int(price * 1e8),    # Phemex attend des valeurs entières pour le prix (basé sur 1e8)
            "type": order_type,
            "clOrdID": client_order_id,
            "product": product_type
        }
        
        query_string = f"clOrdID={client_order_id}&symbol={symbol}"
        
        signature = self._generate_signature(path, query_string)
        
        headers = {
            "x-phemex-request-body": json.dumps(request_body),
            "x-phemex-request-signature": signature,
            "x-phemex-request-expiry": str(int(time.time()) + 60),
            "x-phemex-request-chain": "UNIX",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(f"{self.base_url}{path}", headers=headers, json=request_body)
            response.raise_for_status() # Lève une exception si la réponse est une erreur HTTP
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Erreur lors de la création de l'ordre: {e}")
            return None
