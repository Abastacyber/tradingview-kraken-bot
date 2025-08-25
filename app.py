# app.py
import os
import time
import logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Dict, Optional, Tuple, List
from flask import Flask, request, jsonify
import krakenex

# ========= Couleurs (ANSI) =========
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
RED = "\x1b[31m"

# ========= Logger lisible sur Render =========
logger = logging.getLogger("tv-kraken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False

# ========= Flask =========
app = Flask(__name__)

# ========= Config depuis l'env =========
BASE = os.getenv("BASE", "BTC").upper()   # ex: BTC
QUOTE = os.getenv("QUOTE", "EUR").upper() # ex: EUR
BASE_ALIASED = "XBT" if BASE == "BTC" else BASE  # Kraken utilise XBT pour BTC

SIZE_MODE = os.getenv("SIZE_MODE", "fixed_eur").lower()   # "fixed_eur" | "auto_size"
FIXED_EUR_PER_TRADE = Decimal(os.getenv("FIXED_EUR_PER_TRADE", "50"))
MIN_EUR_PER_TRADE  = Decimal(os.getenv("MIN_EUR_PER_TRADE", "10"))
BTC_RESERVE        = Decimal(os.getenv("BTC_RESERVE", "0.00005"))
FEE_BUFFER_PCT     = Decimal(os.getenv("FEE_BUFFER_PCT", "0.002"))  # 0.2% buffer

HTTP_RETRIES   = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_BACKOFF_S = float(os.getenv("HTTP_BACKOFF_S", "0.7"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "0"))
_last_fire_ts = 0.0

# Activer du “paper trading”
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "on"}

# ========= Kraken client =========
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
    logger.warning(f"{YELLOW}ATTENTION: KRAKEN_API_KEY/SECRET manquants — les ordres réels échoueront.{RESET}")
k = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

# ========= Cache paire =========
# -> (pair_key, price_decimals, lot_decimals, ordermin_base)
_pair_cache: Optional[Tuple[str, int, int, Decimal]] = None


def iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def log_tag_for_request() -> None:
    """
    Ajoute une ligne taggée en tête de log pour rendre l'origine claire
    (PING Uptime / PING Google / ALERTE TradingView).
    """
    ua = (request.headers.get("User-Agent") or "").lower()
    path = request.path
    method = request.method

    tag = None
    # Google Apps Script (webhook TV via Apps Script)
    if "google-apps-script" in ua or "google" in ua and "apps-script" in ua:
        tag = f"{CYAN}PING Google{RESET}"
    # UptimeRobot
    elif "uptimerobot" in ua:
        tag = f"{CYAN}PING Uptime{RESET}"
    # Notre ping interne TV
    elif path == "/webhook" and method == "POST":
        tag = f"{BOLD}ALERTE TradingView{RESET}"
    # /health ou autre
    elif path == "/health":
        tag = f"{DIM}HEALTH{RESET}"

    if tag:
        logger.info(tag)


# ========= Helpers Kraken avec retry/backoff =========
def _kraken_call(api_fn: str, data: Optional[Dict] = None, private: bool = False) -> Dict:
    last_err = None
    for i in range(HTTP_RETRIES):
        try:
            if private:
                resp = k.query_private(api_fn, data or {})
            else:
                resp = k.query_public(api_fn, data or {})
            if resp.get("error"):
                last_err = resp["error"]
                # Si rate limit -> backoff puis retry
                if any("Rate limit" in e or "EAPI:Rate limit" in e for e in resp["error"]):
                    time.sleep(HTTP_BACKOFF_S * (i + 1))
                    continue
                # Erreurs non transitoires -> stop direct
                break
            return resp
        except Exception as e:
            last_err = str(e)
            time.sleep(HTTP_BACKOFF_S * (i + 1))
    raise RuntimeError(f"Kraken {api_fn} error after retries: {last_err}")


def resolve_pair() -> Tuple[str, int, int, Decimal]:
    """
    Résout la paire Kraken réelle (clé ex: 'XXBTZEUR') à partir de BASE/QUOTE.
    Retourne (pair_key, price_decimals, lot_decimals, ordermin_base).
    """
    global _pair_cache
    if _pair_cache:
        return _pair_cache

    alt_wanted = f"{BASE_ALIASED}{QUOTE}"  # ex: XBTEUR
    resp = _kraken_call("AssetPairs", private=False)
    result = resp.get("result", {})
    chosen_key = None
    price_decimals = 5
    lot_decimals = 5
    ordermin = Decimal("0.00001")

    for key, meta in result.items():
        altname = meta.get("altname")
        if altname == alt_wanted:
            chosen_key = key
            price_decimals = meta.get("pair_decimals", price_decimals)
            lot_decimals = meta.get("lot_decimals", lot_decimals)
            # ordermin peut ne pas être présent suivant version d’API
            ordm = meta.get("ordermin")
            if ordm:
                try:
                    ordermin = Decimal(str(ordm))
                except InvalidOperation:
                    pass
            break

    if not chosen_key:
        # fallback: essayer l'altname direct (souvent accepté par AddOrder)
        chosen_key = alt_wanted

    _pair_cache = (chosen_key, int(price_decimals), int(lot_decimals), ordermin)
    return _pair_cache


def get_balances() -> Dict[str, Decimal]:
    resp = _kraken_call("Balance", private=True)
    return {k: Decimal(v) for k, v in resp.get("result", {}).items()}


def public_price_fallback(pair_key: str) -> Optional[Decimal]:
    """Récupère un prix indicatif si TradingView ne l’a pas envoyé."""
    try:
        resp = _kraken_call("Ticker", {"pair": pair_key}, private=False)
        result = resp.get("result", {})
        if not result:
            return None
        first = next(iter(result.values()))
        last = first.get("c", [None])[0]  # last trade price
        if last is None:
            return None
        return Decimal(str(last))
    except Exception:
        return None


def quantize_qty(qty: Decimal, lot_decimals: int) -> Decimal:
    fmt = "0." + "0" * lot_decimals
    return qty.quantize(Decimal(fmt), rounding=ROUND_DOWN)


def place_market_order(side: str, volume_base: Decimal) -> Dict:
    """
    Exécute un ordre marché (ou dry-run si DRY_RUN=True).
    Retourne le champ 'result' de Kraken (ou dict vide en dry-run).
    """
    pair_key, _, lot_decimals, _ = resolve_pair()
    vol_str = str(quantize_qty(volume_base, lot_decimals))
    data = {
        "pair": pair_key,
        "type": side,          # "buy" | "sell"
        "ordertype": "market",
        "volume": vol_str,
    }
    if DRY_RUN:
        # 'validate': True -> Kraken valide/arrondit sans exécuter
        data["validate"] = True

    logger.info(f"{YELLOW}ORDER {side.upper()} {pair_key} vol={vol_str}{RESET}")
    resp = _kraken_call("AddOrder", data, private=True)
    logger.info(f"{GREEN}KRAKEN OK | {resp.get('result', {})}{RESET}")
    return resp.get("result", {})


# ========= Routes =========
@app.get("/health")
def health():
    log_tag_for_request()
    return jsonify({"status": "ok", "time": iso_now()}), 200


@app.post("/webhook")
def webhook():
    global _last_fire_ts
    log_tag_for_request()

    try:
        data = request.get_json(force=True, silent=True) or {}
        signal = str(data.get("signal", "")).upper()
        symbol = str(data.get("symbol", ""))    # ex: BTCEUR (informatif)
        timeframe = str(data.get("timeframe", ""))
        raw_price = data.get("price")           # peut être str/float/None

        logger.info(f"{BOLD}ALERT {signal} | {symbol} {timeframe} | price={raw_price}{RESET}")

        # --- Cooldown anti-spam ---
        if COOLDOWN_SEC > 0:
            now = time.time()
            if now - _last_fire_ts < COOLDOWN_SEC:
                left = int(COOLDOWN_SEC - (now - _last_fire_ts))
                logger.info(f"{YELLOW}Cooldown actif -> alerte ignorée ({left}s restants){RESET}")
                return jsonify({"ok": True, "skipped": "cooldown"}), 200

        # ---- Résolution de la paire + métadonnées precision/min
        pair_key, price_decimals, lot_decimals, ordermin_base = resolve_pair()

        # ---- Prix (utiliser le prix TradingView s’il est fourni, sinon fallback)
        px: Optional[Decimal] = None
        if raw_price is not None:
            try:
                px = Decimal(str(raw_price))
            except InvalidOperation:
                px = None
        if px is None:
            px = public_price_fallback(pair_key)
        if px is None or px <= 0:
            logger.error(f"{RED}Prix introuvable : payload + fallback Ticker KO{RESET}")
            return jsonify({"ok": False, "reason": "NO_PRICE"}), 400

        # ---- Balances
        bals = get_balances()
        bal_eur = bals.get("ZEUR", Decimal("0"))
        bal_btc = bals.get("XXBT", Decimal("0")) or bals.get("XBT", Decimal("0")) or Decimal("0")

        # ---- BUY (on dépense des EUR)
        if signal == "BUY":
            # montant souhaité
            if SIZE_MODE == "fixed_eur":
                eur_to_spend = FIXED_EUR_PER_TRADE
            else:
                eur_to_spend = bal_eur

            if eur_to_spend < MIN_EUR_PER_TRADE:
                logger.warning(f"{YELLOW}REJECT MIN_EUR_PER_TRADE | want={eur_to_spend}{RESET}")
                return jsonify({"ok": False, "reason": "MIN_EUR_PER_TRADE"}), 400
            if bal_eur <= 0:
                logger.warning(f"{YELLOW}REJECT NO_EUR_BALANCE{RESET}")
                return jsonify({"ok": False, "reason": "NO_EUR_BALANCE"}), 400

            # limite à ce qu'on a
            if eur_to_spend > bal_eur:
                eur_to_spend = bal_eur

            # buffer frais et conversion en BASE
            eur_net = eur_to_spend * (Decimal("1") - FEE_BUFFER_PCT)
            vol_base = (eur_net / px)

            # contraintes Kraken : lot_decimals / ordermin
            vol_base = quantize_qty(vol_base, lot_decimals)
            if vol_base <= 0:
                logger.warning(f"{YELLOW}REJECT VOLUME_TOO_SMALL{RESET}")
                return jsonify({"ok": False, "reason": "VOLUME_TOO_SMALL"}), 400
            if vol_base < ordermin_base:
                logger.warning(f"{YELLOW}REJECT BELOW_ORDERMIN | need>={ordermin_base}{RESET}")
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("buy", vol_base)
            _last_fire_ts = time.time()
            # Optionnel : confirmer la clôture (ordre marché -> généralement 'closed' immédiat)
            _log_order_closure(result)
            return jsonify({"ok": True, "kraken": result}), 200

        # ---- SELL (on vend du BTC)
        elif signal == "SELL":
            btc_sellable = (bal_btc - BTC_RESERVE)
            if btc_sellable <= 0:
                logger.warning(f"{YELLOW}REJECT NO_BTC_TO_SELL | bal_btc={bal_btc} | reserve={BTC_RESERVE}{RESET}")
                return jsonify({"ok": False, "reason": "NO_BTC_TO_SELL"}), 400

            vol_base = quantize_qty(btc_sellable, lot_decimals)
            if vol_base < ordermin_base:
                logger.warning(f"{YELLOW}REJECT BELOW_ORDERMIN{RESET}")
                return jsonify({"ok": False, "reason": "BELOW_ORDERMIN"}), 400

            result = place_market_order("sell", vol_base)
            _last_fire_ts = time.time()
            _log_order_closure(result)
            return jsonify({"ok": True, "kraken": result}), 200

        else:
            return jsonify({"ok": False, "reason": "UNKNOWN_SIGNAL"}), 400

    except Exception as e:
        logger.error(f"{RED}ERROR webhook: {type(e).__name__}: {e}{RESET}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _log_order_closure(addorder_result: Dict) -> None:
    """
    Si un ordre vient d’être accepté, on tente d’enquêter:
    - statut 'closed' (via QueryOrders)
    - heure d’exécution, prix/volume
    - pour un SELL, on calcule un PnL réalisé naïf via /pnl pairing FIFO (optionnel)
    """
    if not addorder_result or DRY_RUN:
        return

    txids = addorder_result.get("txid") or []
    if not txids:
        return

    try:
        q = _kraken_call("QueryOrders", {"txid": ",".join(txids)}, private=True)
        orders = q.get("result", {})
        for txid, o in orders.items():
            status = o.get("status")
            descr = o.get("descr", {})
            price = descr.get("price", "market")
            opentm = o.get("opentm")
            closetm = o.get("closetm")
            vol = o.get("vol", "")
            vol_exec = o.get("vol_exec", "")
            fee = o.get("fee", "")

            if status == "closed":
                logger.info(f"{GREEN}CLOSED {txid} | vol_exec={vol_exec} | fee={fee} | price={price} | "
                            f"opened={opentm} | closed={closetm}{RESET}")
            else:
                logger.info(f"{YELLOW}STATUS {txid} | {status}{RESET}")
    except Exception as e:
        logger.warning(f"{YELLOW}Impossible de confirmer la clôture: {e}{RESET}")


# ========= PnL réalisé FIFO =========
@app.get("/pnl")
def pnl():
    """
    Calcule le PnL réalisé (quote) sur N jours (FIFO) pour la paire courante.
    GET /pnl?days=30
    """
    try:
        days = int(request.args.get("days", "30"))
    except Exception:
        days = 30

    pair_key, _, _, _ = resolve_pair()
    start_ts = int(time.time()) - days * 86400

    # On récupère l'historique de trades (fills) côté compte
    resp = _kraken_call("TradesHistory", {"start": start_ts}, private=True)
    trades = resp.get("result", {}).get("trades", {})

    # Filtrer la paire qui nous intéresse
    fills: List[Dict] = []
    for _, t in trades.items():
        if t.get("pair") == pair_key:
            fills.append(t)

    # Trier par time croissant
    fills.sort(key=lambda x: x.get("time", 0))

    # FIFO sur les ACHATS
    buy_queue: List[Tuple[Decimal, Decimal]] = []  # (remaining_vol_base, unit_cost_quote_incl_fees)
    realized_pnl = Decimal("0")
    total_fees = Decimal("0")
    realized_roundtrips = 0

    for f in fills:
        side = f.get("type")         # "buy" | "sell"
        vol = Decimal(str(f.get("vol", "0")))
        cost = Decimal(str(f.get("cost", "0")))   # montant en QUOTE (hors fee)
        fee = Decimal(str(f.get("fee", "0")))
        total_fees += fee

        if side == "buy":
            unit_cost = (cost + fee) / vol if vol > 0 else Decimal("0")
            buy_queue.append((vol, unit_cost))
        else:  # sell
            sell_net = (cost - fee)  # on considère le net encaissement côté QUOTE
            remaining = vol
            sell_value_consumed = Decimal("0")  # coût d'achat des unités vendues

            while remaining > 0 and buy_queue:
                q_vol, q_uc = buy_queue[0]
                take = min(remaining, q_vol)
                sell_value_consumed += q_uc * take
                q_vol -= take
                remaining -= take
                if q_vol == 0:
                    buy_queue.pop(0)
                else:
                    buy_queue[0] = (q_vol, q_uc)

            # si on a réussi à matcher entièrement
            if remaining == 0:
                realized_pnl += sell_net - sell_value_consumed
                realized_roundtrips += 1

    out = {
        "pair": pair_key,
        "days": days,
        "realized_pnl_quote": str(realized_pnl.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "fees_quote": str(total_fees.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "round_trips": realized_roundtrips,
        "time": iso_now(),
    }
    color = GREEN if realized_pnl >= 0 else RED
    logger.info(f"{color}PNL {days}j {pair_key} | realized={out['realized_pnl_quote']} {QUOTE} | "
                f"fees={out['fees_quote']} | trades={realized_roundtrips}{RESET}")
    return jsonify(out), 200


# ========= Entrée gunicorn (Render) =========
# (pas de app.run: Render lance via gunicorn)
