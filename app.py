import os
import sys
from binance.client import Client
from binance.exceptions import BinanceAPIException
import time

# Script de diagnostic pour vérifier la validité des clés API de Binance sur Render.

# Fonction principale pour exécuter le diagnostic
def run_diagnostic():
    print("Démarrage du script de diagnostic...")
    
    # 1. Vérification des variables d'environnement
    api_key = os.environ.get('BINANCE_API_KEY')
    api_secret = os.environ.get('BINANCE_API_SECRET')

    if not api_key:
        print("Erreur : La variable d'environnement 'BINANCE_API_KEY' n'est pas définie.")
        # Utiliser sys.exit(1) pour forcer le déploiement à échouer et afficher le message dans les logs de Render
        sys.exit(1)
    if not api_secret:
        print("Erreur : La variable d'environnement 'BINANCE_API_SECRET' n'est pas définie.")
        sys.exit(1)

    print("Variables d'environnement API détectées avec succès.")
    
    # 2. Création du client et test de connexion
    try:
        # Crée un client Binance avec les clés API
        print("Tentative de création du client Binance...")
        client = Client(api_key, api_secret)
        print("Client créé avec succès.")

        # Tente une requête simple et non sensible pour vérifier la connexion
        print("Tentative de récupération de l'heure du serveur pour valider la connexion...")
        server_time = client.get_server_time()
        print(f"Connexion à l'API de Binance réussie. Heure du serveur : {server_time['serverTime']}")
        
        # Le script se terminera avec succès
        print("Diagnostic terminé avec succès. Les clés API sont valides.")
        
    except BinanceAPIException as e:
        # Gère les erreurs spécifiques de l'API Binance
        print("Échec de la connexion à l'API de Binance.")
        print(f"Code d'erreur : {e.code}")
        print(f"Message d'erreur : {e.message}")
        print("Diagnostic terminé avec échec. Les clés API sont invalides. Veuillez vérifier leur format ou leurs permissions sur le site de Binance.")
        # Forcer le script à échouer pour que l'erreur soit visible dans les logs de Render
        sys.exit(1)
    except Exception as e:
        # Gère toutes les autres erreurs
        print("Une erreur inattendue est survenue.")
        print(f"Détails de l'erreur : {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostic()

