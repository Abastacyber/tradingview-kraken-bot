# Consignes permanentes — projet ORION

## Le propriétaire du projet
- Connaît le trading ; **néophyte en informatique** (GitHub, Render,
  Python, fichiers de config : à expliquer simplement).
- Toujours répondre **en français**, simplement, en expliquant les termes techniques en une phrase.
- Lui donner des actions concrètes et courtes quand il doit faire quelque chose lui-même.
- On construit ensemble : proposer, expliquer, laisser décider sur les choix importants
  (stratégie, argent, passage LIVE).

## Au début de chaque séance
1. Lire `docs/MEMORY_ORION.md` (état actuel + prochaine action).
2. Lire la dernière entrée de `docs/LAB_JOURNAL.md` et le dernier fichier de `docs/veille/`.
3. Récupérer la ou les veilles hebdomadaires non encore enregistrées. La veille automatique
   (tâche programmée « Veille ORION hebdomadaire », id `trig_01XpYiBBrRqTeStQxWpSLrLb`, dimanche 19h36 Paris)
   n'a PAS le droit d'écrire sur GitHub : son rapport complet est le dernier message de sa session.
   → `get_trigger` (outils claude-code-remote) → `last_run.session_id` → `list_events` (kinds assistant)
   → enregistrer le rapport dans `docs/veille/VEILLE_AAAA-MM-JJ.md` et ajouter une ligne datée dans la mémoire.
   Autre tâche programmée : rappel unique le 15/01/2027 (`trig_01QmCxQtv1ZUphsgCVjyQowt`) ; son rapport se récupère de la même façon.
4. ORION tourne EN RÉEL depuis le 25/11/2026 (enveloppe 20 €) : limiter les envois sur `feature/orion-protocol-v1` (chaque envoi redémarre le bot) ;
   tester avant tout envoi ; toute modification des ordres réels = Effort « Élevé » et accord du propriétaire.

## À la fin de chaque séance
- Mettre à jour `docs/MEMORY_ORION.md` : ligne « Dernière mise à jour : JJ/MM/AAAA »,
  tableau d'état, prochaine action, et **une nouvelle ligne datée** dans le journal des mises à jour.
- Toute expérience du laboratoire → entrée datée dans `docs/LAB_JOURNAL.md`.

## Règles de sécurité (non négociables)
- **Ne jamais passer LIVE** (DRY_RUN=false) sans décision GO explicite du propriétaire du projet.
- Ne jamais écrire de secret / clé API dans un fichier, un commit ou un message.
- Une seule modification technique à la fois ; tester avant de continuer (`pytest -q`).
- Ne pas inventer de paramètre de trading : le mesurer dans le laboratoire (`lab/`).
- Toujours raisonner **net de frais** (frais Kraken + glissement).

## Repères techniques
- Bot : `app.py` (Flask + ccxt), déployé sur Render, branche `feature/orion-protocol-v1`.
- Tests : `pytest -q` (dépendances : `requirements.txt` + `requirements-dev.txt`).
- Laboratoire : `lab/` (voir `lab/README.md`), résultats dans `lab/results/`.
- Script de test DRY_RUN pour le propriétaire (PowerShell) : `tools/orion_test_dry_run.ps1`.
