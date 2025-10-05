# TradingView → Kraken Bot (Render)

Flask app qui reçoit des webhooks TradingView et (en papier) calcule une qty selon un risque en EUR. Mapping BTC→USDT pour Binance.

## Variables d'env (Render)
- BASE_SYMBOL=BTC
- QUOTE_SYMBOL=USDT
- PAPER_MODE=1
- RISK_EUR_PER_TRADE=25
- KRAKEN_API_KEY=...
- KRAKEN_API_SECRET=...

## Test
GET /health -> 200 {"status":"ok"}

POST /webhook (JSON):
{"signal":"BUY","symbol":"BTC/EUR","timeframe":"15m"}
