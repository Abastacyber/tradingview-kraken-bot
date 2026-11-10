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
