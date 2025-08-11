import os
import json
from flask import Flask, request, jsonify
import krakenex

app = Flask(_name_)

# ========= Configuration via variables d'environnement =========
API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
PAPER_MODE = os.getenv("PAPER_MODE", "1") == "1"   # 1 = test (papier), 0 = réel

BASE = os.getenv("BASE_SYMBOL", "BTC").upper()     # ex: BTC
QUOTE = os.getenv("QUOTE_SYMBOL", "EUR").upper()   # ex: EUR
RISK_EUR_PER_TRADE = float(os.getenv("RISK_EUR_PER_TRADE", "25"))

# ========= Client Kraken =========
api = krakenex.API(API_KEY, API_SECRET)

# Résolution et cache du code de paire Kraken (ex: "XXBTZEUR")
_PAIR_CACHE = None


def resolve_kraken_pair(base: str, quote: str) -> str:
    """
    Récupère le code de paire Kraken en interrogeant l'API publique AssetPairs.
    On essaie d'abord des correspondances sur altname (ex: BTCEUR) et wsname (ex: BTC/EUR).
    Le résultat est mis en cache pour éviter de recharger à chaque webhook.
    """
    global _PAIR_CACHE
    if _PAIR_CACHE:
        return _PAIR_CACHE

    try:
        resp = api.query_public("AssetPairs")
        if "result" not in resp:
            raise RuntimeError(f"AssetPairs error: {resp}")

        wanted_alt = f"{base}{quote}"          # ex: BTCEUR
        wanted_ws = f"{base}/{quote}"          # ex: BTC/EUR

        for pair_code, meta in resp["result"].items():
            alt = str(meta.get("altname", "")).upper()
            ws = str(meta.get("wsname", "")).upper()
            if alt == wanted_alt or ws == wanted_ws:
                _PAIR_CACHE = pair_code
                print(f"[PAIR] Resolved {base}-{quote} -> {pair_code} (alt={alt}, ws={ws})")
                return pair_code

        # Fallback minimal (peu probable d'être nécessaire)
        raise RuntimeError(f"No Kraken pair found for {wanted_alt}/{wanted_ws}")

    except Exception as e
