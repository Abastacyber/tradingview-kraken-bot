# ORION — JOURNAL DU LABORATOIRE

Chaque expérience est datée, avec la question posée, la réponse des données,
et ce qu'on en retient. On n'efface jamais une entrée : si une conclusion est
remise en cause plus tard, on ajoute une nouvelle entrée qui la corrige.

---

## EXP01 — 10/11/2026 — Frais, horizon de temps et sorties

Rapport complet : `lab/results/EXP01_frais_horizon.md` · Script : `lab/exp01_frais_et_horizon.py`

**Données :** BTC/EUR, janvier 2020 → octobre 2026, bougies de 15 minutes (≈ 239 000).
Étude 2020-2023, contrôle 2024-2026. Aucune optimisation : réglages standards, tous publiés.

### Ce que disent les données

1. **En 15 minutes, tout perd — même sans être mauvais.**
   Le gain brut moyen par trade des stratégies testées est de l'ordre de 0,01 % à 0,1 %.
   Un aller-retour coûte ≈ 0,9 % (taker 0,40 % × 2 + glissement). Le coût est 10 à 80 fois
   plus grand que l'avantage. Résultat : capital détruit (-95 % à -100 %) sur chaque période.

2. **Les sorties ORION actuelles (stop 3 %, trailing +0,4 % / 0,2 %) sont perdantes par construction.**
   Sans frais : à peu près à l'équilibre (57 % de trades gagnants). Avec les frais réels :
   -0,9 % par trade, environ 30 trades par mois → ruine. Confirme l'alerte de la Bible (Partie 09).

3. **Passer en ordres limit (maker) aide, mais ne sauve pas le 15 minutes.**
   Perte par trade divisée par ~2, toujours largement négative.

4. **Les stratégies de tendance deviennent rentables à partir du 4 heures, et nettement en journalier.**
   Exemples, période de contrôle 2024-2026, ordres market 0,40 % :
   - EMA 20/50 + filtre EMA200, **4 h** : +37 %, perte maximale -20 %, profit factor 1,83, 28 trades.
   - Donchian 55/20, **4 h** : +29 %, perte maximale -30 %, 40 trades.
   - EMA 20/50 + filtre EMA200, **1 jour** : +46 %, perte maximale -21 %, 7 trades.
   - Acheter et conserver (référence) : +77 %, perte maximale -52 %.

5. **Le retour à la moyenne (RSI2, héritage Connors d'AEGIS) ne survit pas aux frais** sur aucun horizon testé.

6. **Si les frais Kraken ont réellement doublé (taker 0,80 %, non confirmé)**, seule la tendance
   journalière et l'EMA 4 h restent positives ; le reste devient nul ou perdant.

### Ce qu'on en retient (leçons)

- La question n°1 n'est pas « quel indicateur ? » mais « **quel horizon permet de battre les frais ?** ».
- La tendance en 4 h / 1 jour gagne **moins** que l'achat-conservation en marché haussier, mais avec
  une perte maximale 2 à 2,5 fois plus faible et en n'étant investi que 15 à 25 % du temps.
- **Prudence statistique :** 7 à 40 trades sur la période de contrôle, c'est peu. Ce sont des pistes,
  pas des preuves. Prochaine étape : tests de robustesse (réglages voisins, année par année, tirages aléatoires).

### Questions ouvertes

- Frais réels du compte Kraken aujourd'hui (voir le compte, ou un relevé de trade récent).
- Peut-on garder un signal 4 h / 1 jour et utiliser le 15 min uniquement pour mieux placer l'entrée ?
- Taille de position selon la volatilité (risquer un montant fixe, pas investir un montant fixe).

---

## EXP02 — 11/11/2026 — Robustesse du suivi de tendance (4 h et journalier)

Rapport complet : `lab/results/EXP02_robustesse.md` · Script : `lab/exp02_robustesse.py`

**Question :** les bons résultats d'EXP01 en 4 h / 1 jour sont-ils solides ou dus à la chance ?
Trois tests : réglages voisins (49 réglages par horizon, chacun avec 2 niveaux de frais), année par année, 10 000 tirages aléatoires.

### Ce que disent les données

1. **Un seul candidat solide : le croisement de moyennes EMA en JOURNALIER.**
   Sur 32 réglages voisins, 100 % sont positifs en étude ET en contrôle (frais 0,40 %),
   97 % encore avec des frais doublés à 0,80 %. Résultat médian en contrôle 2024-2026 : +33 %
   (+25 % à 0,80 %), profit factor médian 2,4. C'est un « plateau », pas un pic de chance.
2. **Le 4 h est fragile.** EMA : 56 % des réglages positifs sur les deux périodes, Donchian 41 %.
   Avec des frais à 0,80 %, quasiment tout devient perdant. → Le 4 h dépend trop des frais.
3. **La cassure Donchian en journalier est moyenne** : 59 % de réglages positifs en contrôle.
4. **Année par année (EMA 20/50 + EMA200, journalier, frais 0,40 %) vs acheter et garder :**
   2020 +211 % (B&H +261 %) · 2021 +41 % (+69 %) · **2022 0 % (B&H -63 %)** · 2023 +25 % (+148 %) ·
   2024 +33 % (+125 %) · **2025 -4 % (B&H -18 %)** · **2026 +12 % (B&H -9 %)**.
   → Elle gagne moins dans les fortes hausses, mais **évite l'essentiel des chutes**. C'est son rôle.
5. **Tirages aléatoires sur 2 ans** (journalier EMA) : 14 % de risque de perdre, résultat médian +54 %,
   perte maximale médiane -8 %, mauvais cas -18 %. 4 h : 25-31 % de risque de perdre, pertes max -21 à -47 %.

### Leçons

- Le cœur le plus prometteur pour ORION est un **suivi de tendance en journalier** : peu de trades
  (≈ 3 par an), donc les frais pèsent peu, même s'ils ont doublé.
- **Limite importante :** ≈ 18 trades sur 7 ans. La robustesse vient du plateau de réglages, pas du nombre
  de trades. Il faudra confirmer sur d'autres marchés (ETH, autres périodes) avant d'y engager de l'argent.
- Un bot à 3 trades par an ne « fait » pas grand-chose : son intérêt est de **protéger le capital dans les
  marchés baissiers** tout en captant une bonne partie des hausses.

### Prochaines questions (EXP03)

- Taille de position selon le risque (risquer 1 % du capital par trade au lieu d'investir 100 %).
- Combiner : tendance journalière comme « feu vert », et le 4 h / 15 min uniquement pour mieux entrer.
- Même test sur ETH/EUR pour vérifier que ce n'est pas propre au BTC.

---

## EXP03 — 11/11/2026 — Préparer le gardien de tendance journalier (voie A choisie)

Rapport complet : `lab/results/EXP03_gardien_journalier.md` · Script : `lab/exp03_gardien_journalier.py`

**Décision du propriétaire (11/11) :** voie A — ORION devient un gardien de tendance journalier.

### Ce que disent les données

1. **Entrée « état » > entrée « croisement ».** « État » = être acheteur tant que EMA rapide > EMA lente
   ET prix > EMA200 (et pas seulement le jour du croisement).
   - BTC/EUR, sortie sur signal seul : 100 % des 16 réglages gagnants sur les 2 périodes, même à 0,80 % de frais.
     Contrôle 2024-2026 médian **+68 %** (acheter et garder +77 %), perte max médiane **-34 %** (B&H -52 %).
   - **ETH/EUR (confirmation sur un autre marché) :** « état » 100 % de réglages gagnants, médian **+40 %**
     alors qu'acheter et garder fait **-5 %** sur la même période. L'entrée « croisement » échoue sur ETH
     (3 trades seulement, ratés). → **la règle « état » se généralise, la règle « croisement » non.**
2. **Sorties :** avec l'entrée « état », le trailing 4 ATR est **nuisible** (allers-retours, frais). La sortie
   sur signal (EMA rapide repasse sous la lente) est la meilleure. Un stop de protection à 3 ATR coûte un peu
   (BTC +62 % au lieu de +68 %) mais protège contre un krach brutal ou une panne du serveur.
3. **Signal au 10/11/2026 :** BTC 66 005 € sous EMA20 (69 449), EMA50 (70 249) et EMA200 (66 909)
   → conditions d'achat **non réunies** : le gardien serait en euros. ETH : idem.

### Règle candidate « ORION GARDIEN v1 » (à implémenter, puis valider en DRY_RUN)

- Données : bougies journalières Kraken BTC/EUR, décision une fois par jour après la clôture (00:00 UTC).
- Achat si EMA20 > EMA50 et clôture > EMA200 (réglage central d'un plateau de 16 réglages tous gagnants).
- Vente si EMA20 < EMA50.
- Stop de protection posé directement chez Kraken (≈ 3 ATR) : une assurance, pas un outil de sortie.
- Capital : uniquement l'enveloppe ORION définie (jamais tout le solde Kraken).

### Limites

- Peu de trades (6 à 10 sur la période de contrôle). Robustesse = plateau de réglages + 2 marchés.
- Le passé ne garantit pas l'avenir : prévoir une perte maximale possible **d'au moins -50 %** de l'enveloppe.

---

## EXP04a — 20/11/2026 — Le taux de financement (levier des traders) aide-t-il le gardien ?

Rapport : `lab/results/EXP04a_funding.md` · Script : `lab/exp04a_funding.py` · Données : `lab/data_funding.py`

**Hypothèses fixées avant de regarder les résultats** (seuils = quantiles 80 % / 90 % de 2020-2023 uniquement) :
H1 ne pas acheter quand le levier est extrême · H2 investir 50 % quand il est élevé · H3 vendre quand il est extrême.

### Ce que disent les données

- **BTC : aucune variante ne bat le gardien seul** sur la période de contrôle (0 à 31 % des réglages), à 0,80 % comme à 0,40 % de frais.
  Le dosage à 50 % réduit un peu la perte maximale (-32 % au lieu de -35 %) mais coûte beaucoup de rendement (+41 % au lieu de +58 %).
- **ETH : H1 (veto q80) bat le gardien en contrôle (100 % des réglages) mais fait pire en étude (0 %)** → résultat incohérent = hasard. Rejeté.
- H3 (vendre sur levier extrême) est la pire : en tendance haussière, l'euphorie dure souvent plus longtemps qu'on ne le croit.

### Leçons

- **Rejeté : le taux de financement n'apporte rien au gardien journalier.** La tendance contient déjà l'information.
- Un indicateur « suivi par les pros » n'est pas automatiquement utile : c'est exactement pourquoi tout passe par le laboratoire.
- Méthode validée : hypothèses écrites avant, seuils calculés sur la seule période d'étude, critère de cohérence étude + contrôle + 2 marchés.

### Suite (EXP04b)

Indice Peur & Avidité, positions COT (CFTC), volatilité implicite (Deribit), flux ETF — nécessite d'autoriser de nouvelles sources
de données dans l'environnement (accord du propriétaire). Puis indicateurs AEGIS (Bollinger, Heikin Ashi) comme aide à l'entrée.

---

## EXP04b — 20/11/2026 — Autres marchés + données institutionnelles et de sentiment

Rapport : `lab/results/EXP04b_marches_institutionnels.md` · Script : `lab/exp04b_marches_et_institutionnels.py` · Données : `lab/data_alt.py`

### A. Le gardien sur d'autres marchés (16 réglages voisins, frais 0,80 %)

| Marché | Réglages gagnants étude ET contrôle | Gardien contrôle | Acheter-garder contrôle | Verdict |
|---|---|---|---|---|
| BTC/EUR | 100 % | +58 % (perte max -35 %) | +75 % (-52 %) | ✅ robuste |
| ETH/EUR | 94 % | +32 % (-46 %) | -6 % (-67 %) | ✅ robuste |
| BNB/EUR | 100 % | +97 % (-47 %) | +116 % (-58 %) | ✅ robuste (disponibilité sur Kraken à vérifier) |
| XRP, DOGE | 12 % / 0 % | positif en contrôle mais perdant en étude | | ❌ pas fiable |
| SOL, ADA | 25 % | perdant | | ❌ |
| EUR/USD (devise) | 0 % | -14 % | +1 % | ❌ trop calme : les mouvements ne couvrent pas les frais |
| Or (PAXG) | 0 % | +68 % en contrôle, mais perdant en 2021-2023 (marché sans tendance) | +89 % | ❌ non robuste |

→ **La règle fonctionne sur les grandes cryptos liquides à longues tendances (BTC, ETH, BNB), pas partout.**
Pas une formule universelle : chaque marché doit passer le test avant d'être tradé.

### B. Filtres « institutionnels » et de sentiment (BTC et ETH, frais 0,80 %)

- **Peur & Avidité** : aucun effet sur BTC (les achats du gardien arrivent rarement en avidité extrême) ;
  sur ETH, le veto est nuisible et le dosage incohérent (mieux en contrôle, pas en étude). ❌ Rejeté.
- **Volatilité implicite (DVOL)** : aucun effet. ❌ Rejeté.
- **Positions COT des gestionnaires d'actifs** : réduire la mise quand les fonds réduisent leur position **diminue la perte
  maximale dans 100 % des réglages, sur BTC et ETH** (BTC -32 % au lieu de -35 % ; ETH -34 % au lieu de -46 %).
  Mais sur BTC le rendement baisse nettement (+38 % au lieu de +58 %). Rendement/risque meilleur sur ETH, moins bon sur BTC.
  ⚠️ Incohérent entre marchés → **non adopté**, gardé comme **candidat à revoir** (outil de réduction du risque, pas de performance).

### Leçons

- Le cœur simple (tendance journalière) reste le meilleur. **Ajouter des indicateurs « de pros » n'a rien amélioré de façon fiable.**
- La vraie piste d'amélioration suivante est probablement la **diversification** : gardien sur BTC + ETH (+ BNB si disponible sur Kraken),
  qui ne chutent pas toujours en même temps → à tester (EXP05).
- Reste à tester : indicateurs AEGIS (Bollinger, Heikin Ashi) pour mieux placer l'entrée.

---

## EXP05 — 20/11/2026 — Diversifier le gardien (BTC seul / BTC+ETH / BTC+ETH+BNB)

Rapport : `lab/results/EXP05_diversification.md` · Script : `lab/exp05_diversification.py`

Enveloppes à parts égales, un gardien par marché, 16 réglages voisins. Critère fixé avant : meilleur rapport
rendement ÷ perte max que BTC seul, sur l'étude ET le contrôle, pour la majorité des réglages.

### Ce que disent les données (période de contrôle 2024-2026)

| Portefeuille | Ordres limit 0,40 % | Ordres market 0,80 % | Réglages meilleurs que BTC seul |
|---|---|---|---|
| BTC seul | +70 %, perte max -34 % | +58 %, -35 % | — |
| BTC + ETH | +61 %, -35 % | +48 %, -35 % | 25-31 % ❌ |
| BTC + ETH + BNB (rééquilibrage annuel) | **+78 %, -30 %** | **+67 %, -32 %** | 62 % ✅ (100 % en étude) |

- **BTC + ETH n'aide pas** : les deux bougent trop ensemble (corrélation 0,81).
- **Ajouter BNB aide** (corrélation plus faible, 0,66) : un peu plus de rendement et une perte max plus faible. Le critère
  est rempli, mais de peu en contrôle (62 %). La période d'étude (100 %) est flattée par l'envolée des altcoins de 2021.
- **Ordres limit vs market : +12 points sur BTC en contrôle** (+70 % au lieu de +58 %). Confirme le choix des ordres limit.

### Leçons et décision proposée

- Gardien v1 = **BTC seul** (le plus simple et le plus solide). Panier BTC + ETH + BNB = **variante v2 candidate**,
  une fois le gardien BTC validé en réel.
- Prudence sur BNB : c'est le jeton de la plateforme Binance → risque propre à une seule entreprise.

---

## EXP06 — 20/11/2026 — Calibrer le stop de protection du gardien (avant l'étape 5)

Rapport : `lab/results/EXP06_stop_protection.md` · Script : `lab/exp06_stop_protection.py`
Moteur : ajout des frais/glissement propres aux sorties par stop (`Costs.fee_stop`, `Costs.slip_stop`) et du verrou
« pas de rachat après un stop tant que le signal ne s'est pas remis à zéro » (`Exits.no_reentry_after_stop`), avec 2 tests.

Hypothèses écrites avant : achats/ventes sur signal en ordres limit 0,40 % + 0,05 % ; sortie par stop = taker 0,80 %
+ 0,50 % de glissement. Scénario PANNE : la vente sur signal arrive 7 jours en retard (serveur arrêté), seul le stop chez Kraken agit.
Critère fixé avant : (a) ≥ 90 % de réglages gagnants sur 2 périodes, (b) ≤ 3 points de rendement médian perdus en étude
ET en contrôle, (c) ≤ 1 déclenchement/an ; parmi les acceptables, la meilleure « pire perte d'un trade » en PANNE ; ETH ne doit pas contredire.

### Ce que disent les données (BTC/EUR, contrôle 2024-2026, médianes sur 16 réglages)

| Stop | Contrôle | Déclenchements/an | Pire trade en PANNE | ETH contrôle (sans stop : +40 %) |
|---|---|---|---|---|
| aucun | +68 % | 0 | -33 % | +40 % |
| initial 3 ATR | +57 % | 0,9 | -16 % | +32 % |
| initial -15 % | +66 % | 0,2 | -17 % | +26 % ❌ |
| initial -20 % | +68 % | 0 | -21 % | +28 % ❌ |
| initial 8 ATR | +68 % | 0 | -38 % | +40 % |
| suiveur 12 ATR | +68 % | 0 | -26 % | +40 % ✅ |
| suiveur -20 % / -25 % du plus haut | +61 % / +67 % (étude divisée par 2) | 1,9 / 0,9 | -20 % / -25 % | +6 % / +4 % ❌ |

1. **Confirmé : un stop qui se déclenche en temps normal COÛTE** (3 ATR, -15 %, suiveurs en %). Le gardien sort déjà
   sur signal ; un stop proche ne fait qu'ajouter des allers-retours taxés.
2. **Le verrou anti-rachat n'aide pas de façon fiable** (aide BTC avec 3 ATR, nuit fortement sur ETH). ❌ Non adopté.
3. **Le critère désigne « initial -20 % » sur BTC, mais ETH le contredit** (contrôle +28 % au lieu de +40 %) : un seuil fixe
   en % ne s'adapte pas à un marché plus agité. → choix rejeté par la règle de confirmation.
4. **Le stop suiveur 12 ATR passe le critère sur BTC ET ETH** : il ne s'est déclenché quasiment jamais en 7 ans
   (coût nul), et dans le scénario PANNE il ramène la pire perte d'un trade BTC de -33 % à -26 %.
5. **Limite importante :** le gain en PANNE n'est pas stable selon le réglage (10 ATR : -17 % ; 12 : -26 % ; 14 : -34 % ;
   16 : -43 %). Il dépend de 1 ou 2 krachs historiques : **les données ne permettent pas de calibrer finement une
   assurance anti-panne**. Ce qui est solide : un stop très large ne coûte rien ; un stop proche coûte.
   12 ATR ≈ 35 à 50 % sous le plus haut selon la volatilité (ATR BTC ≈ 4 % du prix par jour).

### Décision proposée (au propriétaire)

- Stop de protection du gardien v1 = **suiveur 12 ATR** (plus haut depuis l'achat − 12 × ATR14 journalier),
  remonté une fois par jour après la clôture, jamais redescendu, posé chez Kraken. **Une assurance contre la panne
  prolongée ou le krach extrême, pas un outil de sortie.** Pas de verrou anti-rachat.
- Alternative plus simple : stop initial fixe (posé une fois à l'achat, jamais modifié) — mais aucun seuil fixe testé
  n'est à la fois gratuit et utile sur les deux marchés.
