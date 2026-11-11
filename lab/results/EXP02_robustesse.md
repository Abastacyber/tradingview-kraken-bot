# EXPÉRIENCE 02 — Robustesse du suivi de tendance (2026-11-11)

Données : BTC/EUR 2020-01-03 → 2026-10-31. Étude = 2020-2023, contrôle = 2024-2026. Glissement 0,05 % par ordre en plus des frais.

## A. Voisinage des réglages

Chaque ligne = une famille de stratégie testée avec tous ses réglages voisins.

| tf | famille | frais | réglages testés | % positifs étude | % positifs contrôle | % positifs les deux | contrôle : médiane % | contrôle : pire % | contrôle : meilleur % | contrôle : PF médian |
|---|---|---|---|---|---|---|---|---|---|---|
| 1D | Donchian | 0,40 % | 17 | 100.0 | 59.0 | 59.0 | 4.2 | -15.1 | 59.6 | 1.28 |
| 1D | Donchian | 0,80 % | 17 | 100.0 | 47.0 | 47.0 | -6.1 | -25.3 | 50.9 | 1.12 |
| 1D | EMA croisement | 0,40 % | 32 | 100.0 | 100.0 | 100.0 | 32.9 | 6.1 | 109.2 | 2.42 |
| 1D | EMA croisement | 0,80 % | 32 | 100.0 | 97.0 | 97.0 | 25.0 | -0.5 | 94.7 | 2.1 |
| 4h | Donchian | 0,40 % | 17 | 100.0 | 41.0 | 41.0 | -8.7 | -54.9 | 34.0 | 1.05 |
| 4h | Donchian | 0,80 % | 17 | 88.0 | 0.0 | 0.0 | -39.4 | -78.8 | -4.8 | 0.79 |
| 4h | EMA croisement | 0,40 % | 32 | 75.0 | 69.0 | 56.0 | 7.4 | -44.0 | 68.5 | 1.19 |
| 4h | EMA croisement | 0,80 % | 32 | 50.0 | 19.0 | 9.0 | -18.4 | -73.6 | 33.6 | 0.8 |

## B. Année par année (rendement %, frais taker 0,40 %)

| année | Acheter et garder | EMA 20/50 +EMA200 (4h) | Donchian 55/20 (4h) | EMA 20/50 +EMA200 (1D) | Donchian 20/10 (1D) |
|---|---|---|---|---|---|
| 2020 | 261.0 | 63.0 | 119.0 | 211.0 | 230.0 |
| 2021 | 69.0 | 17.0 | 28.0 | 41.0 | 53.0 |
| 2022 | -63.0 | -9.0 | -34.0 | 0.0 | -29.0 |
| 2023 | 148.0 | -9.0 | 65.0 | 25.0 | 12.0 |
| 2024 | 125.0 | 41.0 | 31.0 | 33.0 | 81.0 |
| 2025 | -18.0 | -6.0 | -1.0 | -4.0 | -6.0 |
| 2026 | -9.0 | 4.0 | -1.0 | 12.0 | -12.0 |

## B. Année par année (rendement %, frais taker 0,80 %)

| année | Acheter et garder | EMA 20/50 +EMA200 (4h) | Donchian 55/20 (4h) | EMA 20/50 +EMA200 (1D) | Donchian 20/10 (1D) |
|---|---|---|---|---|---|
| 2020 | 261.0 | 46.0 | 95.0 | 199.0 | 219.0 |
| 2021 | 69.0 | 8.0 | 14.0 | 39.0 | 47.0 |
| 2022 | -63.0 | -16.0 | -41.0 | 0.0 | -31.0 |
| 2023 | 148.0 | -21.0 | 48.0 | 21.0 | 5.0 |
| 2024 | 125.0 | 28.0 | 14.0 | 30.0 | 74.0 |
| 2025 | -18.0 | -12.0 | -10.0 | -7.0 | -9.0 |
| 2026 | -9.0 | -2.0 | -9.0 | 11.0 | -16.0 |

## C. Tirages aléatoires sur 2 ans (10 000 simulations, frais taker 0,40 %)

| stratégie | trades / 2 ans | proba de perdre sur 2 ans | résultat médian | mauvais cas (5 %) | perte max médiane | perte max mauvais cas (5 %) |
|---|---|---|---|---|---|---|
| EMA 20/50 +EMA200 (4h) | 23 | 31 % | +21 % | -31 % | -21 % | -39 % |
| Donchian 55/20 (4h) | 28 | 25 % | +44 % | -36 % | -27 % | -47 % |
| EMA 20/50 +EMA200 (1D) | 5 | 14 % | +54 % | -13 % | -8 % | -18 % |
| Donchian 20/10 (1D) | 11 | 33 % | +30 % | -42 % | -26 % | -49 % |

## C. Tirages aléatoires sur 2 ans (10 000 simulations, frais taker 0,80 %)

| stratégie | trades / 2 ans | proba de perdre sur 2 ans | résultat médian | mauvais cas (5 %) | perte max médiane | perte max mauvais cas (5 %) |
|---|---|---|---|---|---|---|
| EMA 20/50 +EMA200 (4h) | 23 | 50 % | +0 % | -43 % | -28 % | -47 % |
| Donchian 55/20 (4h) | 28 | 41 % | +14 % | -48 % | -34 % | -56 % |
| EMA 20/50 +EMA200 (1D) | 5 | 17 % | +47 % | -16 % | -9 % | -21 % |
| Donchian 20/10 (1D) | 11 | 39 % | +18 % | -46 % | -29 % | -52 % |
