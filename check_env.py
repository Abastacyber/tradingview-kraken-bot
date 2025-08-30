import os
import requests
import json

# Récupérer les variables d'environnement
api_key = os.environ.get('API_KEY')
secret_key = os.environ.get('SECRET_KEY')

print("--- Début du test des variables d'environnement ---")
print(f"La valeur de API_KEY est : {api_key}")
print(f"La valeur de SECRET_KEY est : {secret_key}")

# Si vous voulez vérifier la connexion à Phemex directement depuis ce script de test
# Décommentez le code ci-dessous
# if api_key and secret_key:
#     try:
#         # Exemple de requête simple pour vérifier l'authentification
#         timestamp = int(time.time() * 1000)
#         signed_string = f"/v1/api/account/wallet{timestamp}"
#         signature = hmac.new(secret_key.encode('utf-8'), signed_string.encode('utf-8'), hashlib.sha256).hexdigest()
#         
#         headers = {
#             "x-phemex-access-token": api_key,
#             "x-phemex-request-body": "",
#             "x-phemex-request-signature": signature,
#             "x-phemex-request-expiry": str(timestamp),
#         }
#         response = requests.get("https://api.phemex.com/v1/api/account/wallet", headers=headers)
#         
#         print("\nRésultat de la connexion Phemex avec les variables d'environnement :")
#         print(response.json())
#         
#     except Exception as e:
#         print(f"\nUne erreur s'est produite lors de la connexion : {e}")
# else:
#     print("Erreur : Les variables d'environnement sont vides. Assurez-vous qu'elles sont correctement configurées sur Render.")
