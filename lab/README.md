# ORION LAB — laboratoire de backtest

Le laboratoire teste des idées de trading sur l'historique BTC/EUR **avant** qu'elles
n'arrivent dans ORION. Il ne touche jamais au bot en production ni au compte Kraken.

| Fichier | Rôle |
|---|---|
| `data.py` | télécharge et met en cache l'historique (archives publiques, paire BTC/EUR) |
| `engine.py` | simule les trades : exécution à la bougie suivante, frais, glissement, stops, trailing |
| `strategies.py` | familles de stratégies (tendance, cassure, retour à la moyenne…) |
| `expNN_*.py` | une expérience = une question précise ; produit un rapport dans `results/` |

Conclusions datées : `docs/LAB_JOURNAL.md`.

Lancer (avec Python 3.11+) :

```bash
pip install -r lab/requirements.txt
python lab/exp01_frais_et_horizon.py
```
