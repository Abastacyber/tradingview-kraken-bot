# ORION — test DRY_RUN du correctif sizing (étapes 3 à 7 de la mémoire du 22/08)
# Le secret est saisi masqué, jamais écrit dans la commande.
$base = "https://tv-krkn-bot-v2.onrender.com"
$sec = Read-Host -Prompt "WEBHOOK_SECRET" -AsSecureString
$secret = [System.Net.NetworkCredential]::new("", $sec).Password
$headers = @{ "X-Webhook-Token" = $secret }

# 3. /health (réveille aussi le service Render)
$h = Invoke-RestMethod "$base/health"
$h
if (-not $h.dry_run) { throw "STOP : dry_run=false. Ne pas continuer." }

# Prix BTC/EUR réel (API publique Kraken)
$t = Invoke-RestMethod "https://api.kraken.com/0/public/Ticker?pair=XBTEUR"
$price = [double]$t.result.XXBTZEUR.c[0]

# 4. BUY DRY_RUN quote=25
$now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$buy = @{
  protocol="orion-v1"; signal="BUY"; alert_id="manual-buy-$now"
  symbol="BTC/EUR"; timeframe="15"; confidence=70
  price=$price; stop_price=[math]::Round($price*0.97,1)
  sent_at=$now; quote=25; reason="test sizing dry_run"
} | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri "$base/webhook" -Headers $headers -ContentType "application/json" -Body $buy
$r | Select-Object amount, avg_price, requested_quote, quote_used, sizing_source, stop_source, sl_pct_used, position_id

# 5. Vérification : quantité ≈ 25 / prix (moins FEE_BUFFER_PCT)
"Attendu ~ {0:N8} BTC  |  Reçu {1:N8} BTC" -f (25/$price), $r.amount

# 6. SELL
$now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$sell = @{
  protocol="orion-v1"; signal="SELL"; alert_id="manual-sell-$now"
  symbol="BTC/EUR"; timeframe="15"; confidence=70
  price=$price; sent_at=$now; reason="test sizing dry_run"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/webhook" -Headers $headers -ContentType "application/json" -Body $sell

# 7. État nettoyé : has_position=false, position_id=null
Invoke-RestMethod "$base/health" | Select-Object dry_run, has_position, position_id
Remove-Variable secret, sec
