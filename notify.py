"""
ORION — NOTIFICATIONS sur le téléphone du propriétaire (ntfy, gratuit, sans compte). Ajouté le 25/11/2026.

Réglage sur Render : NTFY_TOPIC = un nom de « sujet » long et aléatoire (il sert de mot de passe : quiconque
le connaît peut lire les notifications). Sans NTFY_TOPIC, rien n'est envoyé.

Règle d'or : une notification ne doit JAMAIS gêner le bot. Envoi en arrière-plan, délai court,
aucune erreur remontée (seulement écrite dans les journaux).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from typing import Callable, List, Optional

log = logging.getLogger("tv-kraken")

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def topic() -> str:
    return (os.getenv("NTFY_TOPIC") or "").strip()


def build(title: str, message: str, priority: int = 3, tags: Optional[List[str]] = None) -> dict:
    return {"topic": topic(), "title": title[:120], "message": message[:1500], "priority": int(priority),
            "tags": tags or []}


def _post(payload: dict, opener: Callable = urllib.request.urlopen) -> bool:
    req = urllib.request.Request(NTFY_SERVER + "/", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(req, timeout=10) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception as e:  # jamais d'exception vers le bot
        log.warning("[NOTIF] envoi impossible : %s", str(e)[:120])
        return False


def send(title: str, message: str, priority: int = 3, tags: Optional[List[str]] = None,
         wait: bool = False, opener: Callable = urllib.request.urlopen) -> bool:
    """Envoie une notification. wait=False : en arrière-plan (ne bloque jamais le bot)."""
    if not topic():
        return False
    payload = build(title, message, priority, tags)
    if wait:
        return _post(payload, opener)
    threading.Thread(target=_post, args=(payload, opener), name="ntfy", daemon=True).start()
    return True


# ----- Messages d'ORION (textes courts, en clair) -----

def for_operation(op: dict) -> tuple:
    t = op.get("type", "")
    res = op.get("resultat_net_trade_pct")
    extra = f" — résultat du trade : {res:+.2f} %" if isinstance(res, (int, float)) else ""
    body = f"{op.get('btc', '')} BTC à {op.get('prix', '')} € ({op.get('montant_eur', '')} €){extra}"
    if t.startswith("ACHAT"):
        return ("🟢 ORION a ACHETÉ du BTC", body, 4, ["green_circle"])
    if "STOP" in t:
        return ("🛡️ Stop de protection exécuté par Kraken", body, 5, ["shield"])
    return ("🔵 ORION a VENDU ses BTC", body, 4, ["blue_circle"])


IMPORTANT_EVENTS = ("stop de protection posé", "NON posé", "a disparu", "refusé", "impossible", "exécuté par Kraken")


def for_event(detail: str) -> Optional[tuple]:
    """Événements du gardien réel qui méritent une notification (pas le « verrou fermé » répété chaque heure)."""
    if not any(k in detail for k in IMPORTANT_EVENTS):
        return None
    if "posé chez Kraken" in detail:
        return ("🛡️ Stop de protection mis à jour", detail, 2, ["shield"])
    return ("⚠️ ORION : à vérifier", detail, 4, ["warning"])
