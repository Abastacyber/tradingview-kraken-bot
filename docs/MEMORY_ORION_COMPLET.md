# ORION — MÉMOIRE COMPLÈTE DU PROJET

**Version : 20/11/2026** · **Statut : simulation uniquement (DRY_RUN) — argent réel (LIVE) verrouillé**

Ce document rassemble tout ce qu'il faut savoir pour reprendre ORION, avec n'importe quel assistant ou seul.
Il complète la mémoire vivante `docs/MEMORY_ORION.md`, mise à jour à chaque séance, qui fait foi en cas d'écart.
**Aucun secret ni clé API n'est écrit ici, et il ne faut jamais en ajouter.**

---

## 0. Reprendre en 30 secondes

1. Aller sur **claude.ai/code** et ouvrir une **nouvelle session**. Les anciennes restent dans l'historique.
2. Choisir le dépôt **Abastacyber/tradingview-kraken-bot**, la branche **feature/orion-protocol-v1** et l'environnement **tradingview-kraken-bot**.
3. Écrire :
   > Reprends le projet ORION : lis CLAUDE.md et docs/MEMORY_ORION.md, récupère la dernière veille, puis fais-moi le point.

**Prochaine action :** le propriétaire ajoute le disque persistant sur Render (étape 4, code prêt le 20/11), puis Claude passe à l'étape 5. Voir § 9.

---

## 1. Le but du projet

Construire **un bot de trading personnel**, conçu de A à Z avec et pour son propriétaire. Il doit être **autonome**, **intelligent** et surtout **rentable une fois les frais payés**. Il doit aussi s'améliorer en continu.

- **Pas de copy trading.** On s'inspire de ce qui a fait ses preuves, mais on construit notre propre système.
- Toute idée passe d'abord par le **laboratoire de backtest**, qui la valide ou la rejette sur des données réelles, frais compris.
- Le propriétaire connaît le trading et est **débutant en informatique**. Il faut tout lui expliquer simplement, en français, et le laisser décider des choix importants : stratégie, argent, passage en réel.

### Philosophie (Bible ORION, conservée)

- **Le capital avant le profit.** Le gain net compte plus que le gain brut.
- **La qualité des trades avant leur nombre**, sans tomber dans l'excès de filtres.
- **Mesurer avant de modifier.** Une seule modification technique à la fois, testée avant la suivante.
- **Journaliser les trades et les refus.** Documenter tout le reste.
- La validation, la décision, la gestion du risque et l'exécution sont des étapes séparées.
- Ne jamais exposer un secret. Toujours simuler (paper trading) avant le réel.

---

## 2. Historique

| Période | Étape |
|---|---|
| Avant 2025 | Indicateur TradingView d'aide au trading manuel (surtout l'or, XAUUSD) |
| PROJECT AEGIS | Confirmations multiples (MACD, Connors RSI, Bollinger, Heikin Ashi, ATR), score long/short, stop ATR, objectifs à 1R et 2R, stop ramené au prix d'entrée, stop suiveur, alertes enrichies |
| Ancien bot Kraken (2025) | Vrais ordres en scalping. **Leçon fondatrice : les frais (~0,8 % par aller-retour) mangeaient la performance** |
| ORION (août 2026) | Serveur Flask sur Render, protocole d'alerte « orion-v1 », validateur, anti-doublon, Bible ORION en 22 parties sur Google Drive |
| 22/08/2026 | Dernier point d'arrêt avant la reprise par Claude : bug de taille d'ordre en simulation non corrigé |
| 07 → 20/11/2026 | Reprise par Claude : correctifs, laboratoire, 5 expériences, choix du « gardien journalier », étapes 1 à 4 (code) |

Documents historiques : Google Drive, dossier **Projet ORION / PROJET ORION V1 / PROJECT ORION BIBLE** (Bible en 22 parties, mémoires d'août, anciens app.py, chapitres AEGIS).

---

## 3. Comment fonctionne ORION (en simple)

### Les acteurs

| Outil | Rôle | Qui le gère |
|---|---|---|
| **Kraken** | Le courtier : exécute les ordres et détient l'argent | Le propriétaire (argent, clés API) |
| **Render** | L'ordinateur en ligne, allumé en permanence, qui fait tourner ORION (`app.py`) | Le propriétaire, rarement (réglages) |
| **GitHub** | Le classeur officiel du code et de son historique. Render s'y met à jour tout seul | Claude |
| **TradingView** | Les graphiques. Ne décide plus rien depuis le 20/11 | Le propriétaire |
| **Laboratoire** (`lab/`) | Le banc d'essai des stratégies. Ne touche jamais au bot ni à Kraken | Claude |

### Les deux circuits

- **Circuit de décision (aujourd'hui) :** chaque heure, ORION lit les bougies journalières de Kraken, calcule le signal du gardien et met à jour son portefeuille **simulé**.
- **Circuit de fabrication :** Claude modifie le code, l'envoie sur GitHub, puis Render redémarre ORION avec la nouvelle version.
- GitHub n'est **jamais** sur le chemin d'une décision de trading.

### Infrastructure

| Élément | Valeur |
|---|---|
| Service Render | « TV-KRKN-Bot-V2 > ORION PROJECT 0.5 », plan **Starter** (7 $/mois, pas de mise en veille) |
| Adresse | https://tv-krkn-bot-v2.onrender.com |
| Déploiement | Automatique depuis GitHub, branche `feature/orion-protocol-v1` |
| Démarrage | gunicorn, 1 processus (`gunicorn.conf.py`) |
| Dépôt | github.com/Abastacyber/tradingview-kraken-bot (public). Autres branches : `main`, `develop`, `archive/orion-v0.9-legacy`, `orion-veille` |
| Clé API Kraken | « API NEXT GEN », créée le 11/11/2026, **sans droit de retrait**. La case « Requête » (lire les fonds) reste à cocher avant le passage en réel |
| Surveillance | Un Google Apps Script appelle `/health` chaque minute. Inutile avec le plan Starter, mais sans danger |

### Les pages à ouvrir dans un navigateur (aucun secret)

| Adresse | Contenu |
|---|---|
| `/health` | État du bot : clés présentes, mode simulation, position de l'ancien flux |
| `/gardien/signal` | Signal du jour : prix, EMA20/50/200, position théorique, ce qu'il manque pour acheter |
| `/gardien/journal` | Décisions des 30 derniers jours et vérifications récentes de l'horloge |
| `/gardien/portefeuille` | Portefeuille **simulé** : enveloppe, euros, BTC, valeur, résultat net, frais, opérations |

---

## 4. La stratégie retenue : « ORION GARDIEN v1 »

**Décision du propriétaire (11/11/2026) :** voie A, ORION devient un **gardien de tendance journalier**.
**Décision du 20/11/2026 :** **ORION calcule lui-même le signal** (option 1). TradingView reviendra plus tard comme « deuxième avis ».

### La règle

- **Données :** bougies **journalières** BTC/EUR de Kraken, seulement les bougies clôturées. La clôture a lieu à 00:00 UTC.
- **Achat :** EMA20 > EMA50 **et** clôture > EMA200.
- **Vente :** EMA20 < EMA50.
- **Entre les deux :** on garde la position.
- **Pas d'achat de rattrapage :** au démarrage, ou si la position a été manquée, on n'achète que si les deux conditions d'achat sont réunies ce jour-là.
- **Ordres :** **limit** (frais 0,40 %) plutôt qu'au marché (0,80 %).
- **Stop de protection** posé chez Kraken, environ 3 ATR. C'est une assurance contre un krach brutal ou une panne, pas l'outil de sortie normal. Il sera fait à l'étape 5.
- **Enveloppe :** le bot n'utilise que le montant qui lui est alloué, jamais tout le solde Kraken.
- **Rythme attendu :** environ 2 à 5 allers-retours par an. Le bot passe de longues périodes en euros : c'est voulu, il protège le capital pendant les chutes.

### État du signal

- **Sortie le 06/11/2026 à 67 989 €**, quand l'EMA20 est passée sous l'EMA50. Le gardien a donc évité la chute qui a suivi.
- **Au 19/11/2026 : EN EUROS.** Clôture 56 468 €, sous l'EMA20 (63 591 €), l'EMA50 (67 151 €) et l'EMA200 (66 332 €).

---

## 5. Les frais Kraken (officiels, vérifiés le 20/11/2026 sur kraken.com/pro/fees)

Le palier dépend du volume échangé sur 30 jours **ou** des avoirs chez Kraken ; le plus favorable s'applique.

| Palier | Condition | Ordre limit (maker) | Ordre au marché (taker) |
|---|---|---|---|
| **1 (palier actuel)** | dès 0 $ | **0,40 %** | **0,80 %** |
| 2 | 2 500 $ de volume | 0,30 % | 0,60 % |
| 3 | 10 000 $ de volume ou 20 000 $ d'avoirs | 0,22 % | 0,38 % |
| 4 | 25 000 $ de volume ou 50 000 $ d'avoirs | 0,20 % | 0,35 % |

- Les frais ont augmenté le **9 juillet 2026**. Avant : 0,25 % / 0,40 %.
- BTC/EUR et ETH/EUR ne bénéficient d'aucune remise.
- Minimums BTC/EUR, d'après l'API Kraken : 0,00006 BTC par ordre, 0,45 € minimum.

---

## 6. Ce que le laboratoire a appris (détail dans `docs/LAB_JOURNAL.md`)

- **Données :** 7 ans de BTC/EUR (2020 → octobre 2026), plus ETH, BNB, SOL, XRP, ADA, DOGE, EUR/USD et l'or (PAXG).
- **Méthode :** étude sur 2020-2023, contrôle sur 2024-2026. Les hypothèses sont écrites **avant** les tests, on teste 16 réglages voisins, les frais et le glissement de prix sont toujours inclus.

| Expérience | Question | Réponse |
|---|---|---|
| EXP01 (10/11) | Quel horizon bat les frais ? | **15 min : perdant par construction.** Les sorties ORION de l'époque perdaient environ 0,9 % par trade. La tendance devient rentable en 4 h et en journalier |
| EXP02 (11/11) | Résultat solide ou coup de chance ? | **Le croisement EMA en journalier est robuste** : tous les réglages voisins sont gagnants, et il restait à 0 % en 2022 quand le BTC faisait -63 %. Le 4 h est fragile |
| EXP03 (11/11) | Entrée, sortie, autre marché ? | L'entrée « état » (acheteur tant que les conditions sont vraies) bat l'entrée au seul croisement. Confirmé sur ETH : +40 % quand ETH fait -5 %. Le stop suiveur nuit |
| EXP04a (20/11) | Le taux de financement (levier des traders) aide-t-il ? | **Non.** Rejeté |
| EXP04b (20/11) | Autres marchés ? Indicateurs « institutionnels » ? | Robuste sur **BTC, ETH, BNB**. Pas sur SOL, ADA, XRP, DOGE, ni **EUR/USD**, ni **l'or**. Peur & Avidité et volatilité implicite sont inutiles. Les positions COT des fonds réduisent la pire baisse mais coûtent du rendement : gardées en réserve |
| EXP05 (20/11) | Diversifier ? | BTC + ETH n'aide pas (corrélation 0,81). **BTC + ETH + BNB aide un peu** : pire baisse de -30 % au lieu de -34 %. Les ordres limit rapportent environ +12 points par rapport aux ordres au marché |

### Résultats de référence (contrôle 2024 → octobre 2026, BTC, gardien « état »)

| | Gardien, ordres limit | Gardien, ordres au marché | Acheter et garder |
|---|---|---|---|
| Rendement | ≈ +70 % | ≈ +58 % | ≈ +75 % |
| Pire baisse | ≈ -34 % | ≈ -35 % | ≈ -52 % |

Année par année (BTC, frais 0,40 %) : le gardien a fait **0 % en 2022** contre -63 % pour l'achat-conservation, **-4 % en 2025** contre -18 %, et **+12 % en 2026** contre -9 %.

### Limites à garder en tête

- Peu de trades : environ 18 en 7 ans sur BTC. La confiance vient du fait que tous les réglages voisins marchent et que la règle tient sur deux marchés.
- Le passé ne garantit pas l'avenir : il faut prévoir une baisse possible d'**au moins -50 %** de l'enveloppe.

---

## 7. État technique (code sur `feature/orion-protocol-v1`)

### Fichiers

| Fichier | Rôle |
|---|---|
| `app.py` | Serveur Flask : routes, ancien flux webhook (achat/vente désactivés), horloge et simulation du gardien |
| `gardien.py` | Signal journalier en Python pur : EMA, rejeu de la règle, journal |
| `gardien_sim.py` | Portefeuille simulé : enveloppe, ordres limit simulés, résultat net de frais |
| `tests/` | 34 tests automatiques (33 passent, 1 ignoré hors ligne : parité labo sur données réelles), **100 % hors ligne** (`pytest -q`) |
| `lab/` | Laboratoire : données, moteur de backtest, stratégies, expériences, rapports dans `lab/results/` |
| `docs/MEMORY_ORION.md` | Mémoire vivante, datée (fait foi) |
| `docs/MEMORY_ORION_COMPLET.md` | Ce document : mémoire complète, pour reprendre de zéro |
| `docs/LAB_JOURNAL.md` | Journal daté des expériences |
| `docs/veille/` | Rapports de veille hebdomadaire |
| `CLAUDE.md` | Consignes permanentes pour l'assistant |
| `tools/orion_test_dry_run.ps1` | Ancien test de l'achat/vente par webhook (obsolète depuis le 20/11) |
| `render.yaml` | **Non utilisé** par Render (les vrais réglages sont dans le tableau de bord). Contient `DRY_RUN: "true"` par sécurité |

### Avancement du gardien

| Étape | État |
|---|---|
| 1. Signal en lecture seule, `/gardien/signal` | ✅ 20/11 ; test de parité exacte avec le laboratoire |
| 2. Horloge horaire et journal, `/gardien/journal` | ✅ 20/11 |
| 3. Enveloppe et portefeuille simulé, `/gardien/portefeuille` | ✅ 20/11 ; 100 € simulés, ordres limit à 0,40 % |
| 4. Mémoire durable | 🟡 **code fait le 20/11** (disque persistant Render, réglage `GARDIEN_DATA_DIR`) ; **reste : ajouter le disque sur Render** (propriétaire). La réconciliation avec Kraken viendra avant le passage en réel |
| 5. Stop de protection posé chez Kraken | à faire |
| 6. Rejeu historique et période de simulation | à faire |
| 7. Micro-réel (proposition : 100 €, décision du propriétaire) | à faire |

### Réglages Render (noms seulement ; les valeurs secrètes ne sont jamais écrites)

- **Sécurité :** `DRY_RUN=true` · `ENABLE_SHORTING=false` · `ALLOW_PAYLOAD_SYMBOL=false`.
- **Secrets**, gérés dans Render uniquement : `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `WEBHOOK_SECRET`.
- **Gardien** (valeurs par défaut du code) :
  - `GARDIEN_CAPITAL_EUR=100` : enveloppe simulée ;
  - `GARDIEN_FEE_PCT=0.004` : frais par ordre ;
  - `GARDIEN_CLOCK_ENABLED=true` et `GARDIEN_CLOCK_SEC=3600` : l'horloge et sa fréquence ;
  - `GARDIEN_DATA_DIR=/tmp` : dossier de stockage du portefeuille simulé et du journal. **À régler sur `/var/data`** (le disque persistant) ;
  - `GARDIEN_STATE_FILE` et `GARDIEN_CHECKS_FILE` : fichiers précis, déduits du dossier (ne pas les régler, sauf besoin particulier) ;
  - `GARDIEN_CACHE_SEC=600` : durée de mémorisation du signal.
- **Webhook :** `WEBHOOK_TRADING_ENABLED=false` par défaut. Les ordres d'achat et de vente de TradingView sont ignorés ; le PING fonctionne.
- **Ancien flux, encore présents :**
  - `TRAILING_ENABLED=false` : aucun stop surveillé ;
  - réglages du stop suiveur très serrés : activation 0,3 %, écart 0,04 % ;
  - `RESTORE_ON_START=false` ;
  - `FIXED_QUOTE_PER_TRADE=20` ;
  - `DRY_RUN_MAX_QUOTE` : 100 par défaut.
- **Restes inutilisés**, à nettoyer plus tard : `BAL_CACHE_TTL_SEC`, `DDOS_LOCKOUT_COOLDOWN_SEC`, `PRIVATE_MIN_INTERVAL_MS`, `SELL_DUST_COOLDOWN_SEC`, `SYMBOL`, `RISK_PCT`, `BASE_RESERVE`, `SELL_SPLIT_CHUNKS`, `tp_pct`.

### Points ouverts techniques

- **Avant tout passage en réel :**
  - mémoire durable ;
  - réconciliation entre Kraken et ORION ;
  - stop de protection posé chez Kraken ;
  - droit « Requête » sur la clé API ;
  - anti-doublon persistant ;
  - vérification de l'écart de prix (`MAX_PRICE_DEVIATION_PCT`, présent mais non branché).
- **À mettre à jour quand le gardien passera en réel :** `FEE_BUFFER_PCT` (0,15 % réglé sur Render, vérifié le 20/11 ; 0,20 % par défaut dans le code) est inférieur aux frais réels.

---

## 8. Environnement de travail de Claude

- **Environnement cloud** « tradingview-kraken-bot ». Sites autorisés :
  - données de marché : `api.kraken.com`, `data.binance.vision` (historiques de prix et taux de financement), `www.deribit.com` (volatilité implicite) ;
  - indicateurs : `api.alternative.me` (Peur & Avidité), `publicreporting.cftc.gov` (positions COT) ;
  - pages Kraken : `www.kraken.com`, `support.kraken.com`.
- **Veille hebdomadaire automatique :** tâche programmée « Veille ORION hebdomadaire », identifiant `trig_01XpYiBBrRqTeStQxWpSLrLb`, chaque dimanche à 19h36 (Paris), avec notification sur le téléphone.
  - Elle ne peut **pas** écrire sur GitHub : son rapport complet est le dernier message de sa session.
  - À chaque séance, Claude le récupère et l'enregistre dans `docs/veille/` (procédure dans `CLAUDE.md`).
- **Connecteur Render** disponible : il permettra de consulter directement les déploiements et les journaux.
- **Google Drive** est accessible en lecture pour la Bible et les anciens documents.

---

## 9. Feuille de route

### Gardien (option 1)

1. **Étape 4 : mémoire durable.** **Choix du 20/11 : disque persistant Render** (environ 0,25 $/Go/mois, 1 Go suffit).
   Écartés : petite base de données (plus chère, plus de code), reconstruction depuis Kraken seule (elle viendra
   *en plus*, comme vérification, avant le passage en réel). Code fait ; reste l'ajout du disque par le propriétaire.
2. **Étape 5 :** stop de protection posé chez Kraken (environ 3 ATR), qui survit à une panne du serveur.
3. **Étape 6 :** rejouer l'historique dans le vrai code du bot, puis plusieurs semaines de simulation en direct.
4. **Étape 7 : micro-réel.** Dépôt de 100 € proposé, décision du propriétaire. Un premier ordre contrôlé, puis vérification de tout (Kraken, frais, état, stop).
5. **Enveloppe définitive :** décision du propriétaire. Règle proposée : un montant dont il accepte de voir la moitié disparaître temporairement. Jamais d'argent emprunté ni d'épargne de précaution.

### Laboratoire (les « variations », à lancer quand le gardien BTC sera validé)

- **v2 :** panier BTC + ETH + BNB (EXP05). Attention au risque propre au jeton BNB, lié à une seule entreprise (Binance).
- **Indicateurs AEGIS** (Bollinger, Heikin Ashi) pour mieux choisir le moment d'entrer quand le journalier est au vert.
- **Taille de position selon la volatilité.**
- **Positions COT des fonds** comme outil de prudence : à revoir avec plus de données.
- **Pine Script « gardien »** pour TradingView : affichage des signaux et « deuxième avis », avec comparaison des listes de trades.

### Actions du propriétaire

- **Ajouter le disque persistant sur Render** (nom `orion-data`, chemin `/var/data`, 1 Go) et le réglage `GARDIEN_DATA_DIR=/var/data`.
- **Mettre en pause** les alertes TradingView de l'ancien Pine Script 15 min (garder le script).
- **Cocher « Requête »** sur la clé API Kraken (avant le passage en réel).
- Ne **rien** déposer sur Kraken avant que Claude annonce l'étape 7.

---

## 10. Règles permanentes (non négociables)

1. **Jamais de passage en argent réel** (`DRY_RUN=false`) sans décision GO explicite du propriétaire.
2. **Jamais de secret** (clé API, mot de passe) dans un fichier, un commit, un message ou une capture d'écran.
   - Le 11/11/2026, un fichier `.env` a été partagé dans la conversation, puis une capture montrant un mot de passe. Tout a été renouvelé.
   - Le 20/11/2026, l'export `.env` de Render a de nouveau été partagé dans la conversation → clé Kraken et WEBHOOK_SECRET **à renouveler**.
   - Pour montrer ses réglages Render : envoyer une capture où les valeurs secrètes sont masquées, jamais le fichier `.env` exporté.
3. **Une seule modification technique à la fois**, testée avec `pytest -q` avant la suivante.
4. **Ne jamais inventer un paramètre de trading** : le mesurer dans le laboratoire.
5. **Toujours raisonner net de frais**, glissement de prix compris.
6. Ne jamais effacer une position de l'état si une vente réelle a échoué.
7. À chaque fin de séance : mettre à jour `docs/MEMORY_ORION.md` (date, état, prochaine action, ligne datée dans le journal), et ajouter une entrée datée dans `docs/LAB_JOURNAL.md` pour toute expérience.

---

## 11. Journal des séances de reprise (novembre 2026)

| Date | Ce qui a été fait |
|---|---|
| 07/11 | Reprise : lecture du Drive et du code déployé. Correctif de la taille d'ordre en simulation (le vrai solde Kraken d'environ 2 € était utilisé), premiers tests automatiques |
| 09/11 | Accès GitHub de Claude activé, correctif envoyé |
| 10/11 | `render.yaml` sécurisé. Création du laboratoire. EXP01. Veille hebdomadaire programmée |
| 11/11 | Rôles TradingView, Render, GitHub et Kraken expliqués. Plan Starter confirmé. Secrets exposés puis renouvelés. Test réel du correctif réussi (25 € → 0,00037 BTC). EXP02, EXP03. **Voie A choisie** |
| 15/11 | Première veille automatique : chute du BTC d'environ 12 %, frais 0,40 / 0,80 % quasi confirmés |
| 18/11 | Veille récupérée, mécanisme de veille corrigé, procédure « nouvelle conversation » ajoutée |
| 20/11 | **Option 1 choisie** (ORION calcule le signal). Gardien étapes 1, 2 et 3. EXP04a, EXP04b, EXP05. Frais Kraken officiels confirmés. Achat/vente par webhook TradingView désactivés |
| 20/11 (soir) | Mémoire complète enregistrée dans le dépôt. **Disque persistant choisi** pour l'étape 4 ; code fait (portefeuille et journal relus au redémarrage, fichier abîmé mis de côté, jamais effacé) |
