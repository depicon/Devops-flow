# Audit UI actuel — NiceGUI, 2026-11-02

Migration complète demandée et approuvée : quatre espaces conservés, composants
NiceGUI, palette bleu/violet, navigation commune et état en mémoire par client.
La configuration Streamlit et ses imports applicatifs/tests sont supprimés.

## Résultat livré

Cartes et champs natifs, notes/preuves repliables, scores et couverture distincts,
suivi explicite, exports issus du même objet, messages neutres et commandes de
navigation ciblant le résultat affiché. Réception ZIP bornée en mémoire, tâches
bloquantes hors boucle UI, protections contre doubles soumissions et retours tardifs.

Les 57 scénarios UI antérieurs sont portés ; 23 cas de transport/cycle de vie ajoutés.
Dernière suite dans une installation neuve sans Streamlit : **454 tests et six
sous-tests réussis**. Chrome : 13 contrôles de parcours avec vrais transferts,
puis redémarrage, clavier, captures et quatre espaces à 390px.

## Rendu et accessibilité vérifiés

- Quatre vues : minimum 14px et 5,17:1 pour les éléments textuels mesurés.
- Aperçu JSON ouvert : minimum 14px et 4,74:1 ; bouton de copie nommé en français.
- Focus visible ; 12 Tab par espace, menu et cases de sources exercés au clavier.
- 390px : aucun débordement horizontal, titres sous l’en-tête.
- Labels flottants Quasar et sous-titres de l’uploader corrigés après mesure.

Captures : [Projets](screenshots/projets.png), [Diagnostic](screenshots/diagnostic.png),
[Plan d’action](screenshots/plan-action.png), [Historique](screenshots/historique.png).
Preuves et limites : [TEST_RESULTS](../context/TEST_RESULTS.md), section Migration NiceGUI.

Linux/Chrome seulement ; pas de certification WCAG exhaustive ni contrôle lecteur
d’écran/autres navigateurs. JSON monospace conservé. Les instructions de recette
manuelle dans ACCEPTANCE permettent un contrôle indépendant.

---

## Historique — audit de consolidation de l’ancienne interface

Les observations suivantes concernent la phase antérieure ; les captures liées
ci-dessus ont depuis été remplacées par les captures NiceGUI actuelles.

# Audit UI V2 — 2026-11-01

Plan d'adaptation approuvé. Audit initial : lecture des pages, clés et tests,
puis quatre espaces ouverts dans Chrome avec une base fictive vide isolée.
Aucune exception observée ; capture initiale dans `/tmp/devops-assist-v2-browser`.
Ce contrôle initial ne valide pas les parcours complets ni l'accessibilité.

## Écarts et traitement prévu

| Écran | Écart initial | Adaptation | Critères |
|---|---|---|---|
| Tous | Thème seul, aucun helper ; contexte et sous-titres hétérogènes | Tokens, en-têtes et sections communs | U01/U02/U05 |
| Projets | Création, démo, liste et fiche empilées | Colonnes, fiche active et accès direct au diagnostic | U06 |
| Diagnostic | Questionnaire, scan, résultats longs ; plusieurs actions sans vue d'ensemble | Sections identifiées, résumé des sources, scores/couverture séparés | U01/U05/U07 |
| Scan | Constats détaillés tous affichés ; traitement sans indicateur | Synthèse et cartes repliables ; spinner réel | U02/U03 |
| Résultats | Versions avant scores ; recommandations repliées sans résumé | Scores d'abord, preuves secondaires, priorités lisibles | U02/U05 |
| Plan d'action | Pas de synthèse ; confirmation perdue après rerun | Statuts comptés, confirmation temporaire ciblée | U03/U05 |
| Historique | Résultats avant les exports ; dates techniques | Actions de téléchargement visibles et détails repliables | U01/U05 |
| README | Cadrage MVP et contexte externe | Installation, parcours, limites et captures | U08 |

## États et clés à préserver

Vide : sans projet, sans questionnaire, sans scan, sans diagnostic, sans action.
Chargement : création/démo, sauvegarde, scan, finalisation, suivi et export ;
aucun indicateur ajouté aux affichages statiques. Erreur : validation ou stockage,
entrée de scan refusée, version illisible ; reprise par correction/réessai ou
sélection d'un diagnostic lisible. Succès : confirmation avec suite explicite.

Clés : `navigation`, `active_project_id`, `open_*`, `calms_*`, `scan_context_*`,
`scan_result_*`, `scan_mode_*`, `scan_path_*`, `include_*`, `diagnostic_*`,
`findings_*`, `action_*`, `history_diagnostic_*`, `roadmap_diagnostic_*`,
`report_*`, `download_*`. Les nouvelles soumissions de formulaires reçoivent
une clé explicite ; les tests positionnels seront adaptés.

Conserver le rendu des formulaires CALMS à chaque rerun ; ne pas conditionner
leurs widgets à une étape qui provoquerait leur suppression par Streamlit.
Les détails statiques peuvent être repliés sans supprimer les widgets.

## Recette après adaptation

Base fictive : création A/B, démo, CALMS inconnu et connu, ZIP direct/enveloppé,
local, refus/changement de source, finalisation seule/combinée ; suivi,
historique, téléchargements réels et redémarrage. Vérifier contrastes calculés,
texte ≥14px, focus et clavier sur les quatre espaces. Comparer captures avant/après.
Les résultats réellement exécutés seront consignés dans TEST_RESULTS et STATE.

## Résultat de l’adaptation

Audit et adaptations V2.2→V2.1→V2.3/4/5→V2.6→V2.7 réalisés selon le plan approuvé.
Rendu livré : quatre espaces avec titres/sous-titres, contexte visible, création
et démo côte à côte, notes/preuves repliables, scores distincts de la couverture,
statuts synthétisés et exports accessibles avant les détails.

Les états vides et opérations réelles ont des retours adaptés. Le chargement
n’est pas applicable à un simple affichage statique ; une consultation figée
ne simule pas de succès d’enregistrement. Les erreurs de saisie/scan proposent
la correction de la source ; un historique illisible reste sélectionnable pour
choisir une autre entrée. Pas de bouton de réessai sans opération réelle.

Preuves : [TEST_RESULTS](../context/TEST_RESULTS.md), section Consolidation UI V2.
431 tests et 6 sous-tests réussis. Quatorze contrôles Chrome avec vrais uploads
et téléchargements, redémarrage et persistance ; clavier et mesures ciblées
sur 255 éléments, 14px minimum et ratio minimal 5,17:1 (hors contrôles désactivés).
À 390px, les quatre titres sont visibles et aucun débordement horizontal observé.

Captures réelles : [Projets](screenshots/projets.png),
[Diagnostic](screenshots/diagnostic.png), [Plan d’action](screenshots/plan-action.png),
[Historique](screenshots/historique.png). Les captures initiales temporaires ont
été inspectées pendant l’audit ; seules les captures finales sont livrées.

Limites : aucune certification exhaustive, lecteur d’écran/autres navigateurs
non vérifiés ; libellés internes natifs anglais et aperçu JSON monospace justifiés
dans JOURNAL. La procédure d’ACCEPTANCE demeure la recette manuelle de référence.

## Profils d’évaluation — recette du 2026-11-02

Routes et formulaires dans l’enveloppe NiceGUI existante, quatre espaces préservés.
Douze contrôles navigateur continus et sept complémentaires après redémarrage ;
transferts réels, versions imposées, import/refus, règles/poids et rapports historiques.
Aucune requête HTTP externe observée. Mesures ciblées de la fiche : 48 textes,
minimum 14px et 4,72:1 ; largeur 390px sans débordement et 12 Tab effectifs.

Captures finales réelles : [fiche profil](screenshots/profils.png),
[fiche mobile](screenshots/profil-mobile.png). Ajustements : date française/UTC,
couverture en pourcentage, pluriel des questions. Les autres écrans et états visuels
ne sont pas tous mesurés ; aucune certification exhaustive ni validation d’un autre
OS/navigateur/lecteur d’écran. Les preuves détaillées figurent dans context/TEST_RESULTS.


## Refonte ergonomie — recette du 2 novembre 2026

Projet ouvert déplacé dans le bandeau permanent, réglages et création dédiés. Navigation avec état actif ; quatre étapes de diagnostic et prochaines actions, sans perte de saisie lors du changement d’étape/apparence. Titres lisibles pour les contrôles, codes dans les détails, méthode et nombre de contrôles expliqués. Éditeur de profils recentré sur les questions, paramètres techniques repliés, pourcentages lisibles.

Neuf groupes de contrôles Chrome réussis avec vrai ZIP et vrais téléchargements ; zéro requête externe/erreur JavaScript. Recette sur base fictive isolée, port 8528. Sur 12 vues/segments et 392 échantillons de contraste : minimum 4,56:1 clair et 5,19:1 sombre, zéro échec final. Textes mesurés ≥14px et largeur globale ≤390px à viewport390. Focus vérifié après vraie frappe Tab. Les valeurs ne constituent pas une certification globale ; autres navigateurs, OS et lecteur d’écran non vérifiés.

Le fond Culture du diagramme suit le thème via CSS, sans style inline Mermaid prioritaire ; fill sombre réellement relevé rgb(21, 27, 35). Nœuds et statuts conservent les couleurs partagées avec les exports. Références et topologie restent dans les rapports ; la vue utilise des titres de présentation lisibles.

Captures fictives inspectées : [diagnostic sombre](screenshots/ergonomie-diagnostic-sombre.png), [réglages](screenshots/ergonomie-reglages.png), [questionnaire clair](screenshots/ergonomie-questionnaire.png), [mobile](screenshots/ergonomie-mobile.png), [flux sombre](screenshots/ergonomie-flux-sombre.png). Relevés : [ergonomie-recette.json](ergonomie-recette.json).

Procédure de vérification manuelle pour un autre environnement : créer un projet fictif ; choisir les trois combinaisons de sources successivement ; saisir puis changer d’étape et d’apparence avant d’enregistrer ; analyser un ZIP puis changer sa source pour vérifier l’invalidation ; vérifier le récapitulatif et la création désactivée ; consulter et filtrer les constats, suivre un lien du flux ; télécharger les rapports ; publier un profil et l’assigner explicitement ; ouvrir les réglages/documentation à390px et au clavier ; recharger pour vérifier l’apparence et les résultats persistés.
