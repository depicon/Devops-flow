# Guide développeur — devops-flow

État vérifié le 4 novembre 2026 par lecture du code et collecte pytest.
Le paquet actuel est `src/devops_flow/` ; `src/devops_assist/` a été renommé.
Les chemins Python ci-dessous sont relatifs à `src/devops_flow/`.
Ce guide décrit l’existant ; une information non établie serait notée `[à compléter]`.

## 1. Les quatre couches et leurs frontières réelles

Le repère de lecture est **ui → services → domaine → db**.
La règle de séparation est : **« la flèche ne remonte jamais »**.
Elle exprime une discipline de responsabilités, pas un graphe exact des imports actuels.
Le domaine ne doit dépendre ni de NiceGUI ni de l’état de session.

| Couche | Rôle et emplacement actuels |
|---|---|
| UI | Pages, callbacks et composants dans `ui/` ; rendu NiceGUI en français. |
| Services | Orchestration répartie dans `ui/state.py`, les callbacks et les modules spécialisés ; aucun paquet `services/`. |
| Domaine | Modèles, calculs CALMS, règles, diagnostic, recommandations, architecture et profils. |
| DB | `ProjectStore` dans `db.py`, complété par des classes de stockage héritées. |

En pratique, les pages peuvent appeler directement `ProjectStore`.
Le stockage importe les modèles et validateurs du domaine ; le domaine pur ne lance pas SQL.
`reports.py` importe toutefois `StoredDiagnostic` depuis `db.py`.
Il ne faut donc pas interpréter les quatre couches comme quatre dossiers strictement isolés.
Les écarts constatés et leurs conséquences figurent en section 8.
L’application est un monolithe local Python/NiceGUI/SQLite, lancé sur `127.0.0.1`.

## 2. Où vit quoi

| Module | Responsabilité | Fichier(s) |
|---|---|---|
| Entrée | Démarrage de l’application | `__main__.py` |
| Shell | Navigation et retours d’opération | `ui/application.py` |
| Session | État par projet, finalisation, cache des rapports | `ui/state.py` |
| Pages | Projets, diagnostic, suivi et historique | `ui/pages/{projects,diagnostic,roadmap,history}.py` |
| Pages annexes | Réglages, profils, documentation | `ui/pages/{settings,profiles,documentation}.py` |
| Design system | Tokens et composants partagés | `ui/theme.py`, `ui/components.py` |
| Rendu spécialisé | Résultats, architecture, flux, upload | `ui/{results,architecture,flow,upload}.py` |
| Modèles | Projet, constats et preuves | `models.py` |
| CALMS | Questionnaire, brouillon et scores | `calms.py`, `data/calms_questions.json` |
| Profils | Référentiel versionné et import/export | `profiles.py`, `profile_io.py` |
| Acquisition racine | Façades historiques et contrôles de fichiers | `scanner.py` |
| Acquisition projet | Scan ZIP/local multi-composants | `project_scanner.py` |
| Signaux | Contrats et scanners Python, Node, Go, Java, Rust | `signals/` |
| Architecture | Composants, chemins et relations explicites | `architecture.py`, `architecture_links.py` |
| Règles | Évaluation des indices techniques | `rules.py` |
| Diagnostic | Snapshot racine et dispatch multi-composants | `diagnostic.py` |
| Diagnostic projet | Construction et validation multi-composants | `project_diagnostic.py` |
| Recommandations | Sélection et priorité initiale | `recommendations.py`, `data/recommendations.json` |
| Suivi | Actions, statuts et validations | `roadmap.py` |
| Flux DevOps | Modèle de flux et rendu Mermaid | `diagram.py`, `project_flow.py` |
| Rapports | Projection contrôlée et rendus Markdown/JSON | `reports.py` |
| Stockage principal | Projets, diagnostics, actions, profils | `db.py` |
| Stockage associé | Acquisition distante et documentation | `project_content.py` |
| Déclarations | Validation et stockage des surcharges de stack | `component_overrides.py` |
| Distant | Acquisition GitLab/GitHub | `remote.py` |
| Documentation | Modèles et génération PDF | `documentation.py`, `documentation_pdf.py` |
| Trello | Client, projection, orchestration et stockage | `integrations/` |
| Démo | Projet fictif et archive embarquée | `demo.py`, `data/demo_repository/`, `data/demo_scenario.json` |

`ProjectStore` hérite de `ProjectContentStore`, `TrelloStore` et `ArchitectureStore`.
Les fichiers de stockage associés utilisent donc aussi sa connexion transactionnelle.
Le dépôt audité est une entrée en lecture seule, jamais un environnement à exécuter.
Les acquisitions filtrent les chemins et contenus ; elles ne lisent pas librement tout le dépôt.

## 3. Patterns à reconnaître avant une modification

**Dataclasses gelées.** `Project`, `Finding`, `FindingEvidence`, `DiagnosticSnapshot`,
`EvaluationProfile`, `StoredDiagnostic` et `ReportExport` utilisent `frozen=True`.
Une modification de valeur passe notamment par `dataclasses.replace`.
Cela ne signifie pas que toutes les classes sont immuables : `Session` et `ProjectState`
sont des dataclasses mutables, destinées à l’état d’interface.
Le gel d’une dataclass ne rend pas automatiquement immuable tout objet imbriqué.

**Snapshot.** `DiagnosticSnapshot` encapsule du JSON et expose `to_json()`,
`to_dict()` et `from_json()`. Une lecture produit des données décodées.
`save_diagnostic()` valide ce JSON sans relancer le moteur.
Les diagnostics sauvegardés sont relus sans recalcul avec les catalogues courants.
Les actions de suivi restent modifiables séparément du snapshot initial.
Les lecteurs prennent en charge les formats historiques et le format multi-composants.

**Profil versionné.** La référence est `slug:version`, par exemple `baseline:1`.
`EvaluationProfile` porte le questionnaire et les contrôles applicables.
`publish_profile()` enregistre une nouvelle version ; des triggers SQLite protègent
la définition des versions publiées contre modification et suppression.
Le diagnostic embarque le profil utilisé ; changer celui du projet ne réécrit pas l’historique.
Ne pas confondre version du profil et versions des contrats (`diagnostic-v3`, `report-v4`, etc.).

**Transactions.** `ProjectStore._connection()` active les clés étrangères,
entre dans `with connection`, puis ferme la connexion dans `finally`.
Le contexte SQLite commit en sortie normale et rollback en cas d’exception.
Il n’existe pas d’appel explicite à `commit()` dans les deux flux d’écriture ci-dessous.

## 4. Flux : créer un projet

1. Dans `ui/pages/projects.py`, `render()` propose « Nouveau projet » vers `/projects/new`.
2. `render_new()` affiche nom, description et sélecteur de profil, alimenté par `list_profiles()`.
3. Le clic « Créer le projet » appelle le callback local `create()`, enveloppé par
   `Shell.action()` de `ui/application.py` pour le retour d’opération.
4. `create()` choisit le profil puis lance `store.create_project()` via `nicegui.run.io_bound`.
5. Dans `db.py`, `ProjectStore.create_project()` appelle `Project.new()` de `models.py`.
   Le nom est nettoyé et requis (120 caractères maximum), la description limitée à 2 000 caractères.
   Le modèle reçoit un UUID et des dates UTC.
6. `_connection()` ouvre SQLite ; `BEGIN IMMEDIATE` démarre la transaction d’écriture.
7. `_get_profile()` vérifie le profil demandé ; un profil absent ou archivé provoque une erreur.
   `replace()` fixe `profile_ref`, puis une requête paramétrée insère la ligne `projects`.
8. La sortie de `_connection()` commit, ferme la connexion et rend le `Project` créé.
9. Le callback appelle `Session.open_project(result.id)` puis `ui.navigate.to('/')`.

Un nom invalide échoue avant insertion ; une erreur dans la transaction entraîne un rollback.
Les points d’entrée à lire ensemble sont `render_new()`, `create_project()` et `Project.new()`.

## 5. Flux : finaliser un diagnostic

1. `ui/results.py:render_results()` affiche « Créer le diagnostic » et son callback `finalize()`.
2. Via `Shell.action()`, ce callback appelle `ui/state.py:Session.finalize(project_id)`.
3. La session verrouille `ProjectState.lock`, mémorise les sources sélectionnées et leur révision,
   charge le profil et, si demandé, le brouillon CALMS enregistré avec `load_calms()`.
4. Une source sélectionnée indisponible, une révision changée ou un profil changé interrompt le flux.
5. `diagnostic.py:build_diagnostic()` reçoit réponses, scan et profil.
   Un `ScanResultV2` est délégué à `project_diagnostic.py:build_project_diagnostic()`.
   Le moteur assemble scores, constats, recommandations et snapshot ; l’absence de toute source est refusée.
6. La session calcule une empreinte du snapshot, augmentée de l’acquisition distante si présente.
   Si elle change, `ProjectState.new_submission()` renouvelle la soumission.
7. `run.io_bound` appelle `db.py:ProjectStore.save_diagnostic()` avec la clé de soumission,
   le snapshot, la date attendue du brouillon et l’identifiant d’acquisition éventuel.
8. Le stockage revalide le snapshot, entre en `BEGIN IMMEDIATE` et recherche une soumission existante.
   Une répétition pour le même projet retourne le diagnostic déjà enregistré.
9. Il contrôle projet, profil actif, profil embarqué, brouillon attendu et révision des déclarations.
10. `_insert_diagnostic()` insère `diagnostics`, puis les actions issues des recommandations.
    `save_diagnostic()` rattache aussi l’acquisition distante lorsqu’elle est fournie.
11. La sortie normale du contexte commit l’ensemble ; une erreur annule les écritures de la transaction.
12. La session marque la soumission terminée et sélectionne le diagnostic dans « Diagnostic guidé ».

Le suivi ultérieur passe par les actions persistées ; il ne remplace pas le snapshot.

## 6. Flux : exporter un rapport

1. `ui/pages/history.py:render()` choisit le diagnostic via `ui/selection.py:select_diagnostic()`.
2. La page appelle `Session.report(project_id, diagnostic_id)` dans `ui/state.py`.
3. `db.py:ProjectStore.diagnostic_state()` charge le diagnostic et ses actions ordonnées.
4. `Session.report()` calcule une empreinte et consulte le cache du projet.
   Un changement d’état ou `refresh=True` reconstruit le rapport avec `reports.py:build_export()`.
5. `build_export()` projette les champs autorisés du snapshot et le suivi actuel des actions
   dans un `ReportExport` JSON figé, daté à l’export ; il ne relance pas le scanner.
6. La page prépare `render_markdown(report)` et `render_json(report)` à partir du même objet.
7. Le clic « Télécharger Markdown » ou « Télécharger JSON » appelle `ui.download.content()`
   avec les octets UTF-8 et le nom `devops-flow-<projet>-<diagnostic>.md` ou `.json`.
8. « Actualiser la date du rapport » force `Session.report(..., refresh=True)` puis rafraîchit la page.

Le contenu est préparé au rendu de la page, avant le clic de téléchargement.
Les deux formats partagent données et date ; aucune écriture du rapport en SQLite n’est effectuée.
`report-v4` inclut l’architecture multi-composants et les chemins relatifs validés.
Les rapports historiques conservent leur projection contrôlée ; aucun fichier brut n’est recopié.
Le PDF de documentation relève de `documentation_pdf.py`, pas de ce flux de rapport diagnostic.

## 7. Tests par module

Collecte exécutée : `.venv/bin/python -m pytest --collect-only -q`.
**842 cas collectés dans 47 fichiers**, paramétrisations incluses.
Ce nombre décrit la collecte, pas une exécution réussie ; les sous-tests d’exécution ne sont pas ajoutés.
Les noms ci-dessous sont relatifs à `tests/` et indiquent le module ou comportement couvert.

| Fichier de tests | Cas collectés |
|---|---:|
| `test_architecture_models.py` | 13 |
| `test_calms.py` | 35 |
| `test_calms_profile_aware.py` | 6 |
| `test_component_overrides.py` | 4 |
| `test_db.py` | 9 |
| `test_db_profiles.py` | 5 |
| `test_demo.py` | 8 |
| `test_diagnostic.py` | 37 |
| `test_diagnostic_profile.py` | 5 |
| `test_diagnostic_storage.py` | 6 |
| `test_diagram.py` | 26 |
| `test_documentation.py` | 20 |
| `test_documentation_pdf.py` | 5 |
| `test_flow_signals.py` | 16 |
| `test_profile_io.py` | 18 |
| `test_profiles.py` | 34 |
| `test_project_diagnostic.py` | 10 |
| `test_project_reports_flow.py` | 3 |
| `test_project_scanner.py` | 15 |
| `test_project_scanner_security.py` | 17 |
| `test_recommendations.py` | 39 |
| `test_remote.py` | 43 |
| `test_rename_compatibility.py` | 5 |
| `test_reports.py` | 17 |
| `test_reports_diagram.py` | 5 |
| `test_reports_profile.py` | 9 |
| `test_roadmap.py` | 25 |
| `test_rules.py` | 108 |
| `test_rules_profile_aware.py` | 2 |
| `test_scanner.py` | 92 |
| `test_stack_scanners.py` | 23 |
| `test_trello.py` | 31 |
| `test_trello_projection.py` | 28 |
| `test_ui_architecture.py` | 3 |
| `test_ui_calms.py` | 6 |
| `test_ui_components.py` | 8 |
| `test_ui_ergonomics.py` | 14 |
| `test_ui_flow.py` | 6 |
| `test_ui_history.py` | 12 |
| `test_ui_journey.py` | 10 |
| `test_ui_lifecycle.py` | 6 |
| `test_ui_profiles.py` | 7 |
| `test_ui_remote_docs.py` | 7 |
| `test_ui_scan.py` | 11 |
| `test_ui_smoke.py` | 8 |
| `test_ui_trello.py` | 8 |
| `test_ui_upload.py` | 17 |

Pour une modification, lancer d’abord le fichier ciblé, puis les suites concernées.
Les tests UI utilisent les fixtures de `tests/conftest.py` ; certains contrôles nécessitent Chromium.
Aucun test ni code applicatif n’a été modifié pour ce guide.

## 8. Dettes constatées par revue rapide

1. **Services mêlés à la session UI** — `ui/state.py` orchestre finalisation et rapports
   tout en important `nicegui.run` : réutiliser ces parcours hors UI implique ce couplage.
2. **Accès direct des pages au stockage** — `ui/pages/projects.py` et `ui/pages/history.py`
   appellent `ProjectStore` ; la couche de services n’est pas un passage systématique.
3. **Type de stockage importé par les rapports** — `reports.py` importe `db.StoredDiagnostic`.
   La génération, pourtant sans SQL, dépend donc du module de persistance.
4. **Écriture SQL redondante** — `db.py:_insert_diagnostic()` renseigne `profile_ref` dans
   l’INSERT puis exécute un UPDATE du même champ avec la même valeur : une écriture supplémentaire.

La recherche textuelle n’a repéré aucun SQL dans les pages UI, ni import NiceGUI hors `ui/`.
Le premier constat vise l’orchestration située dans l’UI, pas un import NiceGUI dans le domaine pur.
Cette revue ciblée ne constitue pas un audit exhaustif ; les quatre points ne sont pas des bugs démontrés.
