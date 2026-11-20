# ORION — MÉMOIRE VIVANTE

**Dernière mise à jour : 20/11/2026**
**Statut : DRY_RUN uniquement — LIVE verrouillé**

Ce fichier est LA mémoire du projet. Il est mis à jour (avec la date) à chaque séance de travail.
Historique long et philosophie : Bible ORION sur Google Drive (dossier « PROJECT ORION BIBLE »).
Conclusions chiffrées : `docs/LAB_JOURNAL.md`. Veille : `docs/veille/`.
Ne jamais écrire de secret ou de clé API dans ce fichier.

---

## 1. Le but

Un bot de trading personnel, conçu avec et pour le propriétaire, autonome, intelligent et **rentable net de frais**.
Pas de copy trading : on s'inspire de ce qui a fait ses preuves, on construit le nôtre.
Philosophie : TradingView analyse → ORION décide → Kraken exécute → tout est documenté.
Priorités : capital avant profit · net avant brut · qualité sans sur-filtrage · mesurer avant de modifier.

## 2bis. Qui fait quoi (en simple)

Deux circuits distincts :
- **Circuit du trade (temps réel) :** TradingView (Pine Script repère le setup) → message webhook
  (protocole orion-v1 + mot de passe) → Render (app.py = ORION contrôle, décide, dimensionne)
  → Kraken (exécute, détient l'argent).
- **Circuit de fabrication (quand on change le code) :** Claude → GitHub (version officielle du code
  + historique) → Render recharge automatiquement la nouvelle version.
- GitHub n'est **jamais** sur le chemin d'un trade. Le laboratoire (`lab/`) est hors circuit.
- Qui touche quoi : Pine/TradingView = le propriétaire (copier-coller fourni par Claude) ;
  GitHub = Claude ; réglages Render (DRY_RUN, clés) = le propriétaire, rarement, après discussion ;
  Kraken (argent, clés API, frais) = le propriétaire.

## 2. Où on en est

| Élément | État |
|---|---|
| Service Render `tv-krkn-bot-v2` | en ligne, DRY_RUN=true |
| Branche de travail GitHub | `feature/orion-protocol-v1` |
| Sizing DRY_RUN (bug 25 € → 0.00005 BTC) | corrigé ; **test réel PASS le 11/11** : 25 € → 0,00037217 BTC à 67 071 €, sizing_source=dry_run_virtual, stop Pine 3,03 %, SELL + nettoyage d'état OK |
| `render.yaml` DRY_RUN=false (danger) | corrigé le 10/11 → `true` |
| Tests automatiques | 8 tests, tous PASS |
| **Gardien journalier — étape 1** (signal en lecture seule) | **fait le 20/11** : `gardien.py` + page `/gardien/signal` ; parité exacte avec le labo testée ; aucun ordre |
| Laboratoire de backtest | créé le 10/11 (`lab/`), première expérience faite |
| Persistance d'état / réconciliation Kraken | à faire (bloquant avant LIVE) |
| Anti-doublon persistant | à faire |
| `MAX_PRICE_DEVIATION_PCT` | variable présente, non branchée |
| Plan Render | **Starter** (7 $/mois, 0,5 CPU, 512 Mo) : pas de mise en veille — point résolu le 11/11 |
| Déploiement Render | automatique depuis `feature/orion-protocol-v1` (commit 11ff64e en ligne le 11/11 → correctif sizing déployé) |
| `TRAILING_ENABLED` sur Render | **false** → aucun stop surveillé par ORION (ni stop Pine, ni trailing). Acceptable en DRY_RUN, **bloquant avant LIVE** |
| Réglages trailing sur Render | activation 0,3 % / écart 0,04 % (CONF2) : encore plus serrés que le code ; perdants selon EXP01 |
| `RESTORE_ON_START` sur Render | false (l'état n'est pas relu au redémarrage) |
| `render.yaml` du dépôt | ne reflète PAS les réglages réels de Render (fichier non utilisé) |
| Secrets (clés Kraken + WEBHOOK_SECRET) | exposés le 11/11 → **renouvelés le 11/11** (nouvelle clé « API NEXT GEN » sans droit de retrait, ancienne supprimée ; /health OK) |
| Frais Kraken | **confirmés le 20/11** : palier 1 = 0,40 % maker / 0,80 % taker |
| Droit Kraken « Requête » (lire le solde) | non coché sur la nouvelle clé — inutile en DRY_RUN, **nécessaire avant LIVE** |
| Alertes TradingView | mot de passe à mettre à jour s'il y a des alertes actives (non confirmé) |
| Ping `/health` chaque minute (Google Apps Script) | ancien maintien en éveil du plan gratuit ; inutile en Starter mais sans danger |

## 3. Ce que les données nous ont appris (résumé — détail dans LAB_JOURNAL)

- **15 minutes = perdant par construction** avec les frais Kraken : l'avantage brut par trade
  (0,01-0,1 %) est 10 à 80 fois plus petit que le coût d'un aller-retour (≈ 0,9 %).
- **Les sorties ORION actuelles** (trailing +0,4 % / 0,2 %) perdent ≈ 0,9 % par trade → à revoir.
- **Tendance en 4 h et 1 jour : rentable net** sur la période de contrôle 2024-2026,
  avec une perte maximale 2 à 2,5 fois plus faible que l'achat-conservation. Échantillon encore petit.
- **EXP02 (11/11) : le seul candidat robuste est le suivi de tendance EMA en JOURNALIER** — 100 % des réglages
  voisins positifs (97 % avec frais doublés), 0 % en 2022 quand le BTC fait -63 %. Le 4 h est fragile et sensible aux frais.
  Limite : ≈ 18 trades en 7 ans.
- **EXP03 (11/11) : règle candidate « ORION GARDIEN v1 »** — acheteur tant que EMA20>EMA50 et prix>EMA200 (journalier),
  vente quand EMA20<EMA50, stop de protection chez Kraken. Confirmée sur ETH/EUR (+40 % quand ETH fait -5 %).
  Au 10/11/2026 : conditions NON réunies (le gardien serait en euros).
- **EXP04b (20/11) : le gardien est robuste sur BTC, ETH et BNB ; pas sur SOL/ADA/XRP/DOGE, ni EUR/USD, ni l'or.**
  Peur & Avidité et volatilité implicite : inutiles. Positions COT des fonds : réduisent la perte max mais coûtent du rendement
  sur BTC → non adopté, candidat à revoir. Piste suivante : diversification BTC + ETH (EXP05).
- **EXP04a (20/11) : le taux de financement (levier) n'améliore PAS le gardien** (rejeté, voir LAB_JOURNAL).
- **Frais Kraken Pro CONFIRMÉS (page officielle kraken.com/pro/fees, lue le 20/11/2026)** — spot, selon volume 30 j OU avoirs :
  palier 1 (0 $) **0,40 % maker / 0,80 % taker** · palier 2 (2,5 k$ de volume) 0,30 / 0,60 · palier 3 (10 k$ de volume ou 20 k$ d'avoirs)
  0,22 / 0,38 · palier 4 (25 k$ ou 50 k$ d'avoirs) 0,20 / 0,35. BTC/EUR et ETH/EUR ne sont pas dans les paires à remise.
  → Le gardien devra passer des **ordres limit (maker, 0,40 %)** plutôt que market (0,80 %). Moteur du labo réglé par défaut à 0,80 %.
  BNB/EUR existe sur Kraken.

## 4. Prochaine action

1. **Propriétaire :** cocher « Requête » sur la clé API Kraken (avant LIVE). Frais : confirmés, plus rien à fournir.
2. **Claude — construction du gardien (option 1 décidée le 20/11 : ORION calcule lui-même le signal)** :
   ✅ étape 1 signal lecture seule · étape 2 horloge + journal quotidien des décisions · étape 3 enveloppe ORION_CAPITAL
   + exécution DRY_RUN · étape 4 état durable + réconciliation Kraken · étape 5 stop de protection chez Kraken ·
   étape 6 rejeu historique + période DRY_RUN · étape 7 micro-LIVE (100 € proposés, décision du propriétaire).
   En parallèle : EXP04 (indicateurs institutionnels + AEGIS comme filtres/dosage). Plus tard : TradingView en « deuxième avis ».
3. **Décision prise le 20/11 :** ORION calcule lui-même le signal (option 1) ; TradingView reviendra en deuxième avis.

## 4bis. Reprendre dans une nouvelle conversation

Toute la mémoire est dans GitHub (ce fichier, `docs/LAB_JOURNAL.md`, `docs/veille/`, `CLAUDE.md`).
Pour reprendre : claude.ai/code → nouvelle session → dépôt `Abastacyber/tradingview-kraken-bot`,
branche `feature/orion-protocol-v1`, environnement `tradingview-kraken-bot` → écrire :
« Reprends le projet ORION : lis CLAUDE.md et docs/MEMORY_ORION.md, récupère la dernière veille, puis fais-moi le point. »

## 5. Règles permanentes

- Une seule modification technique à la fois, testée avant la suivante.
- Ne jamais passer LIVE sans décision GO explicite du propriétaire du projet.
- Ne jamais inventer un paramètre : le calibrer par les données.
- Ne jamais effacer une position de l'état si une vente réelle a échoué.
- Ne jamais écrire de secret dans un fichier.

## 6. Journal des mises à jour

- **11/11/2026** — Explication des rôles TradingView / Render / GitHub / Kraken (section 2bis). Point à vérifier ajouté : mise en veille du plan gratuit Render.
- **20/11/2026 (4)** — Frais Kraken confirmés sur la page officielle (0,40 % maker / 0,80 % taker au palier 1). Labo par défaut à 0,80 %.
- **20/11/2026 (3)** — EXP04b : autres marchés + Peur & Avidité, COT, volatilité implicite (voir LAB_JOURNAL). Correction affichage € sur /gardien/signal.
- **20/11/2026 (2)** — EXP04a : taux de financement testé comme veto / dosage / sortie → rejeté (n'apporte rien).
  Moteur du labo : investissement partiel possible (paramètre size).
- **20/11/2026** — Option 1 décidée. Gardien étape 1 : `gardien.py` + `/gardien/signal` (lecture seule, 16 tests dont parité labo).
  Signal au 19/11 : EN EUROS (clôture 56 468 €, sous EMA20/50/200), dernier changement le 06/11.
- **18/11/2026** — Veille du 15/11 récupérée (`docs/veille/VEILLE_2026-11-15.md`) : BTC -12 % en 4 jours (58 293 € le 14/11),
  régime baissier, gardien en euros (aurait évité la chute) ; frais 0,40/0,80 % quasi confirmés. La veille automatique
  ne peut pas écrire sur GitHub → corrigée : elle rend son rapport dans sa session, récupéré à chaque séance (CLAUDE.md).
  Procédure « nouvelle conversation » ajoutée (section 4bis). Clôture du 17/11 : 59 026 €, toujours en euros.
- **11/11/2026 (5)** — Voie A décidée (gardien journalier). EXP03 : entrée « état » robuste sur BTC et ETH. Signal actuel : en euros.
- **11/11/2026 (4)** — EXP02 robustesse : tendance EMA journalière robuste, 4 h fragile (voir LAB_JOURNAL).
- **11/11/2026 (3)** — Secrets renouvelés (clé Kraken + WEBHOOK_SECRET). Test DRY_RUN réel du correctif sizing : PASS.
- **11/11/2026 (2)** — Réglages réels Render analysés : plan Starter (pas de veille), déploiement auto confirmé, TRAILING_ENABLED=false, trailing très serré, RESTORE_ON_START=false. Secrets exposés → renouvellement demandé.
- **10/11/2026** — Laboratoire de backtest créé (données BTC/EUR 2020-2026, moteur avec frais/glissement,
  4 tests). EXP01 : le 15 min est perdant net de frais, la tendance 4 h / 1 j est positive.
  `render.yaml` sécurisé (DRY_RUN=true). Découverte : frais Kraken possiblement doublés (à confirmer).
  Mise en place de la veille hebdomadaire automatique et des consignes permanentes (`CLAUDE.md`).
- **09/11/2026** — Accès GitHub de Claude activé ; correctif sizing DRY_RUN poussé.
- **07/11/2026** — Reprise du projet par Claude. Diagnostic du bug sizing (solde Kraken réel ≈ 2 €
  utilisé en DRY_RUN), correctif + 4 premiers tests automatiques. Alerte `render.yaml`.
- **22/08/2026** — Dernier point d'arrêt précédent (mémoire sur Drive : `MEMORY_ORION_2026-08-22.md`).
