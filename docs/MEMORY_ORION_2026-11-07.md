# ORION PROJECT — MEMORY / HANDOFF

**Date du point d'arrêt : 7 novembre 2026**
**Statut : DRY_RUN uniquement — LIVE toujours verrouillé**
**Remplace : MEMORY_ORION_2026-08-22.md (tout ce qui n'est pas modifié ici reste valable)**

---

## 1. Ce qui a été fait le 07/11

### Patch SIZING DRY_RUN — code prêt, testé hors ligne, PAS ENCORE DÉPLOYÉ

Commit local `2b8e5b6` sur `feature/orion-protocol-v1` (au-dessus de `59150ab`).
Le push depuis la session Claude a été refusé : l'application GitHub Claude n'a pas accès au dépôt.
→ à appliquer : le patch `orion_fix_dry_run_sizing.patch` (2 commits).

Changement (uniquement dans le bloc BUY) :

```python
if DRY_RUN:
    quote_to_use = min(requested_quote, DRY_RUN_MAX_QUOTE)   # sizing_source = "dry_run_virtual"
else:
    balances = ex.fetch_free_balance()                        # sizing_source = "kraken_balance"
    avail_quote = float(balances.get(QUOTE_SYMBOL, 0.0))
    usable_quote = max(0.0, avail_quote - QUOTE_RESERVE)
    quote_to_use = min(requested_quote, usable_quote)
```

- Nouvelle variable : `DRY_RUN_MAX_QUOTE` (défaut 100).
- Réponse BUY enrichie : `requested_quote`, `quote_used`, `sizing_source`.
- Nouveau log : `[SIZING] source=… requested=… used=…`.
- Chemin LIVE inchangé.

### Diagnostic confirmé

Reproduit avec un faux exchange : solde EUR réel ≈ 2 € → `quote=25` donnait exactement `0.00005 BTC`
(le solde réel limitait le montant, puis le minimum Kraken le remontait à 0.00005).
Après patch : `25 / 60 000 ≈ 0.000417 BTC`.

### Premiers tests automatiques du projet

`tests/test_dry_run_sizing.py` — hors ligne, aucune clé, aucun appel réseau :

| Test | Résultat |
|---|---|
| BUY DRY_RUN quote=25 ignore le solde réel | PASS |
| Plafond DRY_RUN_MAX_QUOTE | PASS |
| Cycle BUY → SELL → `has_position=false`, `position_id=null` | PASS |
| LIVE consulte toujours le solde réel | PASS |

Lancer : `pip install -r requirements.txt -r requirements-dev.txt` puis `pytest -q`.

---

## 2. Prochaine action exacte à la reprise

1. Appliquer le patch sur `feature/orion-protocol-v1` (commit GitHub).
2. Render : ajouter `DRY_RUN_MAX_QUOTE=100` (facultatif, 100 par défaut).
3. Déployer, puis lancer `tools/orion_test_dry_run.ps1` (secret saisi masqué).
4. Vérifier : `amount ≈ 25 / prix BTC`, `sizing_source = dry_run_virtual`, puis SELL → état nettoyé.
5. Si PASS → noter dans le TEST_LOG, puis passer à la suite de la roadmap (Pine `strategy()` + backtest).

---

## 3. Nouveau point de vigilance relevé : `render.yaml`

Le `render.yaml` du dépôt contient encore des valeurs anciennes, différentes de la config réelle du dashboard Render :

```text
DRY_RUN: "false"            ← dangereux
ALLOW_PAYLOAD_SYMBOL: "true"
LOG_LEVEL: "DEBUG"
FIXED_QUOTE_PER_TRADE: "50"
STATE_FILE: /tmp/bot_state.json
```

Aujourd'hui `/health` renvoie `dry_run: true`, donc le dashboard l'emporte.
Mais une synchronisation Blueprint pourrait réappliquer `DRY_RUN=false` → LIVE.
À corriger dans une modification dédiée : `DRY_RUN: "true"` dans `render.yaml` (et aligner le reste).
Le README est aussi obsolète (Binance / USDT / PAPER_MODE).

---

## 4. Rappel des chantiers restants (inchangés depuis le 22/08)

- Persistance durable de l'état (`/tmp` non durable sur Render).
- Anti-duplicate persistant (`_seen_alerts` en mémoire).
- **Recovery & State Integrity** avant LIVE : persistance + réconciliation Kraken ↔ ORION + reconstruction de la protection (trailing au boot et/ou stop côté Kraken).
- `MAX_PRICE_DEVIATION_PCT` à brancher réellement.
- SELL réel limité à `last_qty`.
- Nettoyage des variables orphelines après stabilisation.

---

# CHECKPOINT DE REPRISE

```text
Render : LIVE (service), DRY_RUN : TRUE
Pair : BTC/EUR — Timeframe : 15 min — Shorting : FALSE
Code déployé : 59150ab
Patch sizing DRY_RUN : PRÊT (2b8e5b6), tests hors ligne PASS, déploiement À FAIRE
```

Ne jamais inclure un secret/API key dans ce fichier.
