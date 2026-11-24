# ORION — MÉMOIRE VIVANTE

**Dernière mise à jour : 24/11/2026**
**Statut : DRY_RUN uniquement — LIVE verrouillé**

Ce fichier est LA mémoire du projet. Il est mis à jour (avec la date) à chaque séance de travail.
Historique long et philosophie : Bible ORION sur Google Drive (dossier « PROJECT ORION BIBLE »).
Vue d'ensemble pour reprendre de zéro : `docs/MEMORY_ORION_COMPLET.md` (ce fichier-ci fait foi en cas d'écart).
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
| Tests automatiques | 98 tests, tous PASS (dont 13 scénarios du gardien réel contre un faux Kraken) (dont rejeu historique bot = labo, qui demande l'historique du labo téléchargé) |
| **Gardien journalier — étape 1** (signal en lecture seule) | **fait le 20/11** : `gardien.py` + page `/gardien/signal` ; parité exacte avec le labo testée ; aucun ordre |
| **Gardien — étape 3** (portefeuille simulé) | **fait le 20/11** : enveloppe `GARDIEN_CAPITAL_EUR` (100 € simulés), ordres limit simulés 0,40 %, `/gardien/portefeuille` ; fichier en /tmp (non durable → étape 4) |
| Webhook TradingView BUY/SELL | **désactivé le 20/11** (`WEBHOOK_TRADING_ENABLED=false`) : le gardien est le seul décideur ; PING fonctionne. Le script `tools/orion_test_dry_run.ps1` (ancien flux) n'est plus utile |
| **Gardien — étape 4** (mémoire durable) | **code fait le 20/11** : dossier `GARDIEN_DATA_DIR` (défaut /tmp) ; portefeuille + journal relus au redémarrage ; fichier abîmé mis de côté, jamais effacé ; `/health` affiche `gardien_stockage_durable`. ✅ **VALIDÉ le 20/11** : disque Render `/var/data` (1 Go) + `GARDIEN_DATA_DIR=/var/data` ; `/health` → `gardien_stockage_durable: true`, `creds_ok: true` (nouvelle clé). Le portefeuille simulé repart de 100 € au 20/11 (premier démarrage sur le disque) |
| **Gardien — étape 5** (stop de protection, SIMULÉ) | **en ligne le 22/11** (option A, EXP06) : stop suiveur = plus haut depuis l'achat − 12 × ATR14 journalier, jamais redescendu ; vente simulée taker 0,80 % + 0,50 % de glissement ; rattrapage des jours manqués (panne) ; contrôle horaire du plus bas du jour Kraken. **Rejeu historique** : le code du bot retrouve exactement les trades du labo (BTC et ETH, 12 et 3 ATR). Visible sur `/gardien/portefeuille`. Aucun ordre réel |
| **Gardien — étape 6** (essais virtuels) | **mode d'essai fait le 22/11** : `/gardien/essai?debut=AAAA-MM-JJ&fin=AAAA-MM-JJ` (+ `panne_debut`, `panne_jours`, `stop_atr`, `capital`) rejoue le VRAI code (signal + stop + portefeuille) sur le passé ; **page lisible `/gardien/essai/page`** (résumé, graphique, opérations, raccourcis, lisible sur téléphone) ; portefeuille d'essai séparé, aucun ordre. Historique fourni : `data/btceur_journalier.csv` (2020-2026) + Kraken. Reste : période de simulation en direct (plusieurs semaines) |
| **Gardien — étape 7 (préparation)** | **24/11** : `gardien_live.py` sait passer achat limit, vente limit et stop « stop-loss » chez Kraken (vérifié contre le vrai ccxt). **Ordre-test** `/gardien/ordre-test?secret=…` : mode **validation** (Kraken examine achat/stop/vente sans exécuter, aucun argent) utilisable maintenant ; mode **réel** (aller-retour ≤ 20 €, stop posé puis retiré) VERROUILLÉ tant que `GARDIEN_ORDRE_TEST_AUTORISE=true` n'est pas réglé sur Render (GO du propriétaire) + `&confirmer=OUI`, 1 fois / 10 min. **Ordres AUTOMATIQUES branchés le 24/11** (gardien réel) derrière un **DOUBLE VERROU** : `DRY_RUN=false` **ET** `GARDIEN_REEL=true` (GO du propriétaire), + réconciliation fraîche non BLOQUANTE pour ouvrir une position (se protéger — stop, vente — reste permis si Kraken est lisible et la position concorde). Achat/vente en limit maker au meilleur prix, replacés chaque heure ; passage en ordre immédiat après 12 h (achat) / 3 h (vente) — **délais opérationnels choisis par Claude, à valider pendant le micro-réel** (`GARDIEN_ACHAT_MAKER_HEURES`, `GARDIEN_VENTE_MAKER_HEURES`). Stop 12 ATR posé chez Kraken et remonté (retirer puis reposer). Enregistrement immédiat après chaque action. Portefeuille réel séparé (`gardien_portefeuille_reel.json`, enveloppe = `GARDIEN_CAPITAL_EUR`), page protégée `/gardien/portefeuille-reel?secret=…`. `/health` affiche `gardien_mode` |
| **Gardien — étape 2** (horloge + journal) | **fait le 20/11** : vérification toutes les heures, `/gardien/journal` (30 derniers jours + vérifications) ; aucun ordre |
| Laboratoire de backtest | créé le 10/11 (`lab/`), première expérience faite |
| Persistance d'état / réconciliation Kraken | persistance ✅ (étape 4) ; **réconciliation codée le 22/11** : `/gardien/reconciliation?secret=…` (lecture seule, protégée par WEBHOOK_SECRET) compare ORION ↔ Kraken (position, euros de l'enveloppe, stop posé, ordres inconnus) → OK / ATTENTION / BLOQUANT. **Automatique depuis le 24/11** : au démarrage puis chaque heure (si clés présentes), résultat enregistré sur le disque, résumé sans montants dans `/health` et `/gardien/journal`. **Verrou** `gardien_real_orders_allowed()` : refuse tout ordre réel si simulation, pas de réconciliation, réconciliation > 2 h ou BLOQUANT — à appeler par les ordres réels de l'étape 7 (il n'y en a pas encore) |
| Anti-doublon persistant | à faire |
| Version de ccxt | **figée le 22/11 : `ccxt==4.5.93`** (test de cohérence) |
| `MAX_PRICE_DEVIATION_PCT` | variable présente, non branchée |
| Plan Render | **Starter** (7 $/mois, 0,5 CPU, 512 Mo) : pas de mise en veille — point résolu le 11/11 |
| Déploiement Render | automatique depuis `feature/orion-protocol-v1` (commit 11ff64e en ligne le 11/11 → correctif sizing déployé) |
| `TRAILING_ENABLED` sur Render | **false** → aucun stop surveillé par ORION (ni stop Pine, ni trailing). Acceptable en DRY_RUN, **bloquant avant LIVE** |
| Réglages trailing sur Render | activation 0,3 % / écart 0,04 % (CONF2) : encore plus serrés que le code ; perdants selon EXP01 |
| `RESTORE_ON_START` sur Render | false (l'état n'est pas relu au redémarrage) |
| `render.yaml` du dépôt | ne reflète PAS les réglages réels de Render (fichier non utilisé) |
| Secrets (clés Kraken + WEBHOOK_SECRET) | exposés le 11/11 → **renouvelés le 11/11** (nouvelle clé « API NEXT GEN » sans droit de retrait, ancienne supprimée ; /health OK) |
| **Secrets — 20/11** | export `.env` de Render partagé dans la conversation → **clés renouvelées le 20/11** par le propriétaire : nouvelle clé Kraken « THE LAST ONE STANDING » (l'ancienne « API NEXT GEN » doit être supprimée) |
| `FEE_BUFFER_PCT` sur Render | 0,0015 (0,15 %), vérifié le 20/11 ; aucun `GARDIEN_*` réglé sur Render |
| Frais Kraken | **confirmés le 20/11** : palier 1 = 0,40 % maker / 0,80 % taker |
| Droits de la clé « THE LAST ONE STANDING » | **24/11** : Requête (fonds) ✅, consulter ordres ouverts/clôturés ✅, créer/modifier ✅, annuler ✅ ; retrait / adresses de retrait / dépôt ❌ (voulu). Solde Kraken ≈ 0,09 € |
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
- **EXP06 (20/11) : stop de protection** — un stop proche (3 ATR, -15 %) coûte du rendement ; le seuil -20 % fixe échoue sur ETH ;
  **suiveur 12 ATR** : coût nul sur BTC et ETH, limite un peu la pire perte en cas de panne serveur (-26 % au lieu de -33 %), mais
  ce bénéfice dépend de 1-2 krachs (non calibrable finement). Verrou anti-rachat : rejeté. Décision du propriétaire attendue.
- **EXP05 (20/11) : diversification** — BTC+ETH n'aide pas (trop corrélés) ; BTC+ETH+BNB aide un peu (perte max -30 % au lieu de -34 %).
  Gardien v1 = BTC seul ; panier BTC+ETH+BNB = variante v2 candidate. Ordres limit : +12 points vs market sur 2024-2026.
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

1. **Propriétaire :** vérifier que `/gardien/journal` garde ses vérifications après la mise à jour du 20/11 (preuve de durabilité) ;
   cocher « Requête » sur la clé API Kraken « THE LAST ONE STANDING » (avant LIVE) ; mettre en pause les alertes de l'ancien Pine 15 min.
2. **Claude — construction du gardien (option 1 décidée le 20/11 : ORION calcule lui-même le signal)** :
   ✅ étape 1 signal lecture seule · ✅ étape 2 horloge + journal · ✅ étape 3 enveloppe ORION_CAPITAL
   + exécution DRY_RUN en ordres limit simulés · ✅ étape 4 état durable (disque Render ; réconciliation Kraken
   avant LIVE) · ✅ étape 5 stop suiveur 12 ATR (simulé, 22/11) · 🟡 étape 6 : mode d'essai fait (22/11), période de simulation en direct en cours · étape 5 stop de protection chez Kraken ·
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

- **24/11/2026 (3)** — **Ordres automatiques du gardien branchés**, verrouillés (DRY_RUN=false ET GARDIEN_REEL=true). Aucun ordre réel tant que le propriétaire ne règle pas les deux. 98 tests.
- **24/11/2026 (2)** — « Requête » coché par le propriétaire. Cause en clair sur `/health`. **Ordre-test** préparé (validation sans argent ; réel verrouillé). 85 tests.
  Délai annoncé au propriétaire pour un premier vrai ordre : 1 à 2 semaines avec l'ordre-test ; les achats du gardien dépendent ensuite du marché.
- **24/11/2026** — Récap au propriétaire. **Réconciliation automatique** (démarrage + chaque heure) et verrou des ordres réels. Signal au 23/11 : EN EUROS (56 125 €). 74 tests.
- **22/11/2026 (4)** — ccxt figé à 4.5.93. **Réconciliation Kraken** (lecture seule) : `/gardien/reconciliation`. Nécessite les droits « Requête des fonds » et
  « Requête des ordres ouverts et des transactions » sur la clé. 71 tests.
- **22/11/2026 (3)** — Essais vérifiés en ligne par le propriétaire (le rejeu 2024→aujourd'hui retrouve la vraie sortie du 06/11 à 67 989 €).
  Correctif : jours de panne affichés exactement. Nouvelle page `/gardien/essai/page`. 58 tests.
- **22/11/2026 (2)** — Étape 5 en ligne. **Étape 6 : mode d'essai** `/gardien/essai` (rejeu du vrai code sur une période passée, panne simulable).
  Mai 2021 : vente sur signal le 13/05 (-18 % sur le trade, acheter-garder -32 % sur la période) ; panne de 7 jours → vente retardée, -6 points ;
  le stop 12 ATR ne se déclenche pas (trop large en 2021, ≈ 58 % sous le plus haut) — conforme à EXP06. 57 tests.
- **22/11/2026** — Option A choisie (stop suiveur 12 ATR). **Étape 5 codée en simulation** + rejeu historique du vrai code du bot sur 2020-2026 :
  mêmes trades que le labo. Veille du 22/11 récupérée (`docs/veille/VEILLE_2026-11-22.md`) : BTC 54 887 € le 21/11, gardien en euros,
  frais inchangés ; à faire avant LIVE : **fixer la version de ccxt** dans `requirements.txt`.
- **20/11/2026 (12)** — EXP06 stop de protection (voir LAB_JOURNAL) : proposition suiveur 12 ATR, décision du propriétaire attendue avant l'étape 5.
- **20/11/2026 (11)** — Contrôle du propriétaire : `/gardien/journal` affichait 0 vérification alors que l'horloge tourne. Cause probable : l'horloge tourne dans un autre processus gunicorn que celui qui répond aux pages (mémoire vive non partagée). Correctif : la page relit le fichier du disque. 35 tests (34 PASS, 1 ignoré).
- **20/11/2026 (10)** — **Étape 4 validée en ligne** : disque `/var/data` ajouté, `/health` → `gardien_stockage_durable: true`, `creds_ok: true`. Prochaine étape Claude : étape 5 (stop de protection chez Kraken).
- **20/11/2026 (9)** — Réglages Render relus (export `.env` partagé → clés renouvelées : nouvelle clé Kraken « THE LAST ONE STANDING »). GO du propriétaire pour mettre l'étape 4 en ligne (fusion dans `feature/orion-protocol-v1`). Disque `/var/data` en cours d'ajout.
- **20/11/2026 (8)** — Reprise en nouvelle session : aucune nouvelle veille (prochaine le 22/11) ; signal au 19/11 toujours EN EUROS.
  Mémoire complète corrigée (`docs/MEMORY_ORION_COMPLET.md`). Étape 4 : **disque persistant Render choisi** par le propriétaire ;
  code fait et testé (34 tests). Reste : ajout du disque sur Render par le propriétaire.
- **11/11/2026** — Explication des rôles TradingView / Render / GitHub / Kraken (section 2bis). Point à vérifier ajouté : mise en veille du plan gratuit Render.
- **20/11/2026 (7)** — Document de reprise complet `docs/MEMORY_ORION_COMPLET.md` remis au propriétaire.
- **20/11/2026 (6)** — Gardien étape 3 : portefeuille simulé + `/gardien/portefeuille`. Webhook BUY/SELL TradingView désactivé (gardien seul décideur).
  Alertes TradingView de l'ancien Pine 15 min à mettre en pause par le propriétaire.
- **20/11/2026 (5)** — Gardien étape 2 (horloge horaire + `/gardien/journal`). EXP05 diversification (voir LAB_JOURNAL).
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
