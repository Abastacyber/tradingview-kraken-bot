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
Trois tests : réglages voisins (98 réglages par horizon), année par année, 10 000 tirages aléatoires.

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
