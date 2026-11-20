# EXPÉRIENCE 04a — Taux de financement et gardien journalier (2026-11-20)

Seuils de levier calculés sur 2020-2023 uniquement. Étude 2020-2023, contrôle 2024 → fin octobre 2026.
16 réglages EMA voisins (rapide 10-30, lente 40-100) + filtre EMA200. Sortie sur signal.

| marché | frais | variante | contrôle médian % | perte max médiane % | % réglages > base (contrôle) | % réglages > base (étude) | trades contrôle (médian) |
|---|---|---|---|---|---|---|---|
| BTC/EUR | 0,80 % | base (gardien seul) | 57.6 | -35.4 | — | — | 8 |
| BTC/EUR | 0,80 % | H1 veto si levier > q80 | 47.2 | -35.4 | 0 | 25 | 8 |
| BTC/EUR | 0,80 % | H1 veto si levier > q90 | 56.2 | -35.4 | 0 | 19 | 8 |
| BTC/EUR | 0,80 % | H2 dosage 50 % si levier > q80 | 40.7 | -32.4 | 31 | 38 | 8 |
| BTC/EUR | 0,80 % | H3 sortie si levier > q90 | 39.8 | -34.8 | 0 | 0 | 9 |
| BTC/EUR | 0,40 % | base (gardien seul) | 68.4 | -33.8 | — | — | 8 |
| BTC/EUR | 0,40 % | H1 veto si levier > q80 | 57.4 | -33.8 | 0 | 25 | 8 |
| BTC/EUR | 0,40 % | H1 veto si levier > q90 | 67.0 | -33.8 | 0 | 19 | 8 |
| BTC/EUR | 0,40 % | H2 dosage 50 % si levier > q80 | 47.1 | -31.2 | 31 | 38 | 8 |
| BTC/EUR | 0,40 % | H3 sortie si levier > q90 | 50.6 | -33.2 | 0 | 0 | 9 |
| BTC/EUR | seuils | q80 = 20 %/an · q90 = 39 %/an |  |  |  |  |  |
| ETH/EUR | 0,80 % | base (gardien seul) | 32.3 | -45.6 | — | — | 6 |
| ETH/EUR | 0,80 % | H1 veto si levier > q80 | 38.9 | -45.6 | 100 | 0 | 6 |
| ETH/EUR | 0,80 % | H1 veto si levier > q90 | 32.3 | -45.6 | 0 | 0 | 6 |
| ETH/EUR | 0,80 % | H2 dosage 50 % si levier > q80 | 16.8 | -41.0 | 6 | 0 | 6 |
| ETH/EUR | 0,80 % | H3 sortie si levier > q90 | 20.4 | -45.6 | 0 | 0 | 7 |
| ETH/EUR | 0,40 % | base (gardien seul) | 40.2 | -44.3 | — | — | 6 |
| ETH/EUR | 0,40 % | H1 veto si levier > q80 | 47.2 | -44.3 | 100 | 0 | 6 |
| ETH/EUR | 0,40 % | H1 veto si levier > q90 | 40.2 | -44.3 | 0 | 0 | 6 |
| ETH/EUR | 0,40 % | H2 dosage 50 % si levier > q80 | 23.1 | -39.8 | 6 | 0 | 6 |
| ETH/EUR | 0,40 % | H3 sortie si levier > q90 | 28.6 | -44.3 | 0 | 0 | 7 |
| ETH/EUR | seuils | q80 = 26 %/an · q90 = 53 %/an |  |  |  |  |  |
