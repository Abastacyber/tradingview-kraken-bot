# ORION — MÉMOIRE VIVANTE

**Dernière mise à jour : 11/11/2026**
**Statut : DRY_RUN uniquement — LIVE verrouillé**

Ce fichier est LA mémoire du projet. Il est mis à jour (avec la date) à chaque séance de travail.
Historique long et philosophie : Bible ORION sur Google Drive (dossier « PROJECT ORION BIBLE »).
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
| Sizing DRY_RUN (bug 25 € → 0.00005 BTC) | corrigé et poussé le 09/11 — **test réel à faire** |
| `render.yaml` DRY_RUN=false (danger) | corrigé le 10/11 → `true` |
| Tests automatiques | 8 tests, tous PASS |
| Laboratoire de backtest | créé le 10/11 (`lab/`), première expérience faite |
| Persistance d'état / réconciliation Kraken | à faire (bloquant avant LIVE) |
| Anti-doublon persistant | à faire |
| `MAX_PRICE_DEVIATION_PCT` | variable présente, non branchée |
| Render plan gratuit : mise en veille après inactivité | à vérifier — peut arrêter la surveillance du stop (bloquant avant LIVE) |

## 3. Ce que les données nous ont appris (résumé — détail dans LAB_JOURNAL)

- **15 minutes = perdant par construction** avec les frais Kraken : l'avantage brut par trade
  (0,01-0,1 %) est 10 à 80 fois plus petit que le coût d'un aller-retour (≈ 0,9 %).
- **Les sorties ORION actuelles** (trailing +0,4 % / 0,2 %) perdent ≈ 0,9 % par trade → à revoir.
- **Tendance en 4 h et 1 jour : rentable net** sur la période de contrôle 2024-2026,
  avec une perte maximale 2 à 2,5 fois plus faible que l'achat-conservation. Échantillon encore petit.
- **Frais Kraken possiblement doublés en juillet 2026** (taker 0,80 % selon plusieurs sites, non confirmé
  par Kraken). À vérifier sur le compte : c'est un paramètre décisif.

## 4. Prochaine action

1. **Propriétaire :** tester le correctif sizing en DRY_RUN (`tools/orion_test_dry_run.ps1`) et regarder
   ses frais réels sur Kraken Pro (palier de frais du compte).
2. **Claude :** EXP02 = robustesse des stratégies de tendance 4 h / 1 jour (réglages voisins,
   année par année, tirages aléatoires) + taille de position selon la volatilité.
3. **Décision à prendre ensemble ensuite :** faire évoluer ORION du 15 min vers un signal 4 h / 1 jour ?
   (le validateur ORION n'accepte aujourd'hui que `timeframe = "15"`).

## 5. Règles permanentes

- Une seule modification technique à la fois, testée avant la suivante.
- Ne jamais passer LIVE sans décision GO explicite du propriétaire du projet.
- Ne jamais inventer un paramètre : le calibrer par les données.
- Ne jamais effacer une position de l'état si une vente réelle a échoué.
- Ne jamais écrire de secret dans un fichier.

## 6. Journal des mises à jour

- **11/11/2026** — Explication des rôles TradingView / Render / GitHub / Kraken (section 2bis). Point à vérifier ajouté : mise en veille du plan gratuit Render.
- **10/11/2026** — Laboratoire de backtest créé (données BTC/EUR 2020-2026, moteur avec frais/glissement,
  4 tests). EXP01 : le 15 min est perdant net de frais, la tendance 4 h / 1 j est positive.
  `render.yaml` sécurisé (DRY_RUN=true). Découverte : frais Kraken possiblement doublés (à confirmer).
  Mise en place de la veille hebdomadaire automatique et des consignes permanentes (`CLAUDE.md`).
- **09/11/2026** — Accès GitHub de Claude activé ; correctif sizing DRY_RUN poussé.
- **07/11/2026** — Reprise du projet par Claude. Diagnostic du bug sizing (solde Kraken réel ≈ 2 €
  utilisé en DRY_RUN), correctif + 4 premiers tests automatiques. Alerte `render.yaml`.
- **22/08/2026** — Dernier point d'arrêt précédent (mémoire sur Drive : `MEMORY_ORION_2026-08-22.md`).
