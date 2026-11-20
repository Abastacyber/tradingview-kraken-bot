# EXPÉRIENCE 06 — Stop de protection du gardien (2026-11-20)

Données : bougies journalières 03/01/2020 → 31/10/2026. Étude 2020-2023, contrôle 2024 → fin. Médianes sur 16 réglages EMA voisins (entrée « état », filtre EMA200).
Coûts : ordres sur signal limit 0,40 % + 0,05 % ; sortie par stop taker 0,80 % + 0,50 % de glissement.
PANNE : la vente sur signal arrive 7 jours en retard (serveur arrêté) ; seul le stop chez Kraken protège.

## Verdict (critère fixé avant les calculs)

Référence sans stop (BTC) : étude 573.8 %, contrôle 68.4 %, PANNE pire trade -33.0 %.

Stops ACCEPTABLES (critères a, b, c remplis) :
- initial 8 ATR (verrou non) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -38.0 %
- initial 8 ATR (verrou oui) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -38.0 %
- initial -20 % (verrou non) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -21.4 %
- initial -20 % (verrou oui) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -21.4 %
- suiveur 12 ATR (verrou non) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -25.8 %
- suiveur 12 ATR (verrou oui) : contrôle 68.4 %, étude 573.8 %, 0.0 stop/an, PANNE pire trade -25.8 %

**Retenu selon le critère : initial -20 %, verrou non.**
Confirmation ETH : contrôle 28.0 % contre 40.2 % sans stop → DÉGRADATION > 3 points ❌ (choix à rediscuter)

## BTC/EUR

| stop | verrou | % gagnants 2 périodes | étude médian % | contrôle médian % | contrôle pire % | perte max contrôle % | pire trade % | stops / an | PANNE pire trade % | PANNE perte max % | PANNE total 2020-26 % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aucun stop | non | 100 | 573.8 | 68.4 | 49.0 | -33.8 | -15.3 | 0.0 | -33.0 | -54.4 | 507.0 |
| initial 3 ATR | non | 100 | 527.1 | 56.8 | 22.0 | -35.5 | -14.4 | 0.88 | -16.1 | -53.6 | 414.2 |
| initial 3 ATR | oui | 100 | 460.0 | 80.0 | 42.5 | -31.4 | -13.9 | 0.73 | -16.1 | -59.8 | 398.8 |
| initial 5 ATR | non | 100 | 567.7 | 67.6 | 41.1 | -33.9 | -15.3 | 0.15 | -24.4 | -49.7 | 475.0 |
| initial 5 ATR | oui | 100 | 567.7 | 67.6 | 44.7 | -33.8 | -15.3 | 0.15 | -24.4 | -49.7 | 489.8 |
| initial 8 ATR | non | 100 | 573.8 | 68.4 | 49.0 | -33.8 | -15.3 | 0.0 | -38.0 | -55.4 | 425.2 |
| initial 8 ATR | oui | 100 | 573.8 | 68.4 | 49.0 | -33.8 | -15.3 | 0.0 | -38.0 | -55.4 | 425.2 |
| initial -15 % | non | 100 | 560.9 | 66.1 | 46.8 | -34.2 | -16.5 | 0.22 | -16.5 | -52.5 | 461.0 |
| initial -15 % | oui | 100 | 560.9 | 66.1 | 46.8 | -34.2 | -16.5 | 0.22 | -16.5 | -51.6 | 459.4 |
| initial -20 % | non | 100 | 573.8 | 68.4 | 46.3 | -33.8 | -15.3 | 0.0 | -21.4 | -49.5 | 506.9 |
| initial -20 % | oui | 100 | 573.8 | 68.4 | 46.3 | -33.8 | -15.3 | 0.0 | -21.4 | -49.5 | 506.9 |
| suiveur 8 ATR | non | 100 | 539.8 | 55.2 | 25.1 | -34.5 | -15.2 | 0.59 | -21.4 | -50.2 | 611.0 |
| suiveur 8 ATR | oui | 100 | 522.6 | 59.3 | 34.9 | -34.7 | -15.2 | 0.59 | -16.6 | -48.0 | 617.0 |
| suiveur 12 ATR | non | 100 | 573.8 | 68.4 | 49.0 | -33.8 | -15.3 | 0.0 | -25.8 | -53.6 | 472.6 |
| suiveur 12 ATR | oui | 100 | 573.8 | 68.4 | 49.0 | -33.8 | -15.3 | 0.0 | -25.8 | -53.6 | 472.6 |
| suiveur -20 % | non | 100 | 290.0 | 60.8 | 38.6 | -35.1 | -15.9 | 1.9 | -20.0 | -57.2 | 277.8 |
| suiveur -20 % | oui | 88 | 272.8 | 12.6 | -6.4 | -34.3 | -15.2 | 1.03 | -20.2 | -55.8 | 136.0 |
| suiveur -25 % | non | 100 | 401.6 | 66.9 | 34.2 | -33.8 | -16.6 | 0.88 | -25.3 | -50.3 | 456.6 |
| suiveur -25 % | oui | 100 | 359.0 | 67.8 | 31.4 | -33.8 | -15.2 | 0.44 | -24.6 | -46.2 | 433.8 |

## ETH/EUR (confirmation)

| stop | verrou | % gagnants 2 périodes | étude médian % | contrôle médian % | contrôle pire % | perte max contrôle % | pire trade % | stops / an | PANNE pire trade % | PANNE perte max % | PANNE total 2020-26 % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aucun stop | non | 100 | 1311.6 | 40.2 | 5.0 | -44.3 | -18.8 | 0.0 | -20.3 | -62.8 | 1292.5 |
| initial 3 ATR | non | 88 | 1200.4 | 32.0 | -11.8 | -42.4 | -18.6 | 0.59 | -19.4 | -65.3 | 812.6 |
| initial 3 ATR | oui | 75 | 1200.4 | 10.7 | -32.9 | -42.4 | -18.6 | 0.59 | -19.4 | -64.0 | 757.6 |
| initial 5 ATR | non | 88 | 1095.6 | 33.1 | -5.4 | -45.8 | -25.6 | 0.15 | -25.6 | -68.0 | 958.0 |
| initial 5 ATR | oui | 88 | 1095.6 | 35.2 | -5.4 | -45.0 | -25.6 | 0.15 | -25.6 | -68.0 | 958.0 |
| initial 8 ATR | non | 100 | 1311.6 | 40.2 | 5.0 | -44.3 | -18.8 | 0.0 | -20.3 | -62.7 | 1336.5 |
| initial 8 ATR | oui | 100 | 1311.6 | 40.2 | 5.0 | -44.3 | -18.8 | 0.0 | -20.3 | -62.7 | 1336.5 |
| initial -15 % | non | 81 | 1174.1 | 25.8 | -12.4 | -43.0 | -16.5 | 0.44 | -16.5 | -65.4 | 928.2 |
| initial -15 % | oui | 75 | 1174.1 | 13.2 | -33.3 | -41.8 | -16.5 | 0.44 | -16.5 | -64.2 | 718.6 |
| initial -20 % | non | 75 | 1120.8 | 28.0 | -27.0 | -46.4 | -21.4 | 0.29 | -21.4 | -66.2 | 923.0 |
| initial -20 % | oui | 50 | 1120.8 | 1.8 | -44.4 | -45.6 | -21.4 | 0.29 | -21.4 | -66.2 | 858.2 |
| suiveur 8 ATR | non | 100 | 1429.8 | 36.2 | 1.5 | -44.8 | -16.2 | 0.66 | -23.7 | -64.5 | 1173.8 |
| suiveur 8 ATR | oui | 100 | 1379.9 | 35.2 | 5.0 | -44.8 | -16.2 | 0.66 | -23.7 | -58.2 | 1070.2 |
| suiveur 12 ATR | non | 100 | 1309.4 | 40.2 | 5.0 | -44.3 | -16.9 | 0.15 | -20.3 | -61.2 | 1151.2 |
| suiveur 12 ATR | oui | 100 | 1309.4 | 40.2 | 5.0 | -44.3 | -16.9 | 0.15 | -20.3 | -61.2 | 1151.2 |
| suiveur -20 % | non | 56 | 723.8 | 6.4 | -24.9 | -53.2 | -21.3 | 4.62 | -21.3 | -73.3 | 365.1 |
| suiveur -20 % | oui | 88 | 117.2 | 14.8 | -6.9 | -41.4 | -16.8 | 1.47 | -20.5 | -55.7 | 87.2 |
| suiveur -25 % | non | 62 | 660.0 | 4.2 | -31.0 | -51.8 | -26.2 | 2.34 | -26.2 | -68.0 | 443.2 |
| suiveur -25 % | oui | 38 | 681.8 | -3.6 | -26.6 | -50.3 | -21.8 | 1.03 | -24.6 | -55.9 | 377.8 |

## Annexe — sensibilité du stop suiveur ATR

### BTC/EUR — ATR14 journalier : médiane 3.9 % du prix (min 1.7 %, max 15.7 %)

| stop | % gagnants 2 périodes | étude médian % | contrôle médian % | contrôle pire % | stops / an | PANNE pire trade % | PANNE perte max % |
|---|---|---|---|---|---|---|---|
| aucun stop | 100 | 573.8 | 68.4 | 49.0 | 0.0 | -33.0 | -54.4 |
| suiveur 10 ATR | 100 | 573.8 | 68.4 | 37.9 | 0.0 | -16.7 | -47.0 |
| suiveur 12 ATR | 100 | 573.8 | 68.4 | 49.0 | 0.0 | -25.8 | -53.6 |
| suiveur 14 ATR | 100 | 573.8 | 68.4 | 49.0 | 0.0 | -34.3 | -59.8 |
| suiveur 16 ATR | 100 | 573.8 | 68.4 | 49.0 | 0.0 | -42.6 | -58.6 |

### ETH/EUR — ATR14 journalier : médiane 5.3 % du prix (min 1.8 %, max 24.0 %)

| stop | % gagnants 2 périodes | étude médian % | contrôle médian % | contrôle pire % | stops / an | PANNE pire trade % | PANNE perte max % |
|---|---|---|---|---|---|---|---|
| aucun stop | 100 | 1311.6 | 40.2 | 5.0 | 0.0 | -20.3 | -62.8 |
| suiveur 10 ATR | 88 | 1562.8 | 40.2 | -8.2 | 0.15 | -23.1 | -62.3 |
| suiveur 12 ATR | 100 | 1309.4 | 40.2 | 5.0 | 0.15 | -20.3 | -61.2 |
| suiveur 14 ATR | 100 | 1072.2 | 40.2 | 5.0 | 0.15 | -28.5 | -68.9 |
| suiveur 16 ATR | 100 | 1311.6 | 40.2 | 5.0 | 0.0 | -20.3 | -62.8 |
