# DevOps Assist

Application web locale d'accompagnement à l'amélioration DevOps, réalisée dans le cadre d'un Master 2. Interface en français, logique métier indépendante de la GUI, données conservées dans SQLite.

**État au 2026-10-30 : MVP local fonctionnel sous Linux, P0–P6 achevés et trois défauts d'audit corrigés.** Suite actuelle : **423 tests et 6 sous-tests réussis**. Navigation/source du scan, analyse Compose partielle et rejet des chemins ZIP profonds vérifiés dans Chrome avec vrais uploads. L'installation neuve et la recette complète P6 datent du 2026-10-20 ; elles n'ont pas été répétées pour ces corrections ciblées. Démo fictive embarquée disponible. Preuves et limites dans [TEST_RESULTS](context/TEST_RESULTS.md). Cette recette technique ne constitue pas une validation scientifique du barème CALMS. Windows/macOS n'ont pas été testés.

## Disponible dans le code

- Créer un projet avec nom obligatoire (120 caractères maximum) et description facultative (2 000 caractères maximum).
- Lister les projets, ouvrir une fiche et retrouver les données depuis SQLite après relance. Deux projets peuvent porter le même nom ; ils ont des identifiants distincts.
- Naviguer entre Projets, Diagnostic guidé, Plan d’action et Historique / Rapports.
- Dans Diagnostic guidé, répondre aux 15 questions CALMS, ajouter des notes facultatives, enregistrer et reprendre le questionnaire du projet, consulter les cinq scores et leur couverture.
- Analyser un ZIP ou un dossier local en lecture seule ; consulter six contrôles et leurs preuves sans contenu brut.
- Finaliser un diagnostic CALMS seul, scan seul ou combiné ; consulter onze constats filtrables, les rapprochements et les recommandations classées par impact/effort. Retrouver le dernier diagnostic enregistré après relance.
- Suivre séparément les actions de chaque diagnostic : statut, commentaire local, ordre et dates persistantes.
- Rouvrir plusieurs diagnostics historiques sans scan ni recalcul ; prévisualiser et télécharger Markdown/JSON depuis le même état daté.

Aucun service IA externe n’est nécessaire.

## Essayer la démo fictive

Dans **Projets**, cliquer sur **Charger une démo**. Un nouveau projet « Démo fictive — équipe Atelier », son questionnaire et son diagnostic sont enregistrés ensemble. Chaque chargement crée un projet distinct ; aucun projet, diagnostic ou suivi existant n'est remplacé. Ouvrir ensuite **Diagnostic guidé**, **Plan d'action** ou **Historique / Rapports**. La mention fictive reste dans les résultats et les rapports.

Le scénario `demo-v1` est embarqué dans le package, sans accès à un dépôt réel ni dépendance à `tests/`. Scores attendus : Culture **22,2**, Automation **100**, Lean **33,3**, Measurement **50** avec couverture **2/3**, Sharing **indéterminé** avec couverture **0/3**. La couverture totale est **11/15**. Les indices ciblés signalent `latest`, USER final non explicite et privilèges ; le job de test reste à confirmer. L'action `REC-CMP-001-RISK` est prioritaire. Une déclaration de tests systématisés est rapprochée prudemment des indices CI, sans modifier le score Automation.

Pour essayer l'import, **Télécharger le ZIP fictif**, ouvrir un projet puis **Diagnostic guidé → Archive ZIP**, choisir ce fichier et **Analyser le dépôt**. Le chargement de démo n'enregistre pas de scan temporaire dans la session : il faut analyser le ZIP pour finaliser volontairement un autre diagnostic technique ou combiné. Ces configurations sont des données synthétiques à analyser, jamais à exécuter.

## Utiliser CALMS

Créer ou ouvrir un projet, puis choisir **Diagnostic guidé**. Remplir les cinq onglets (Culture, Automation, Lean, Measurement, Sharing) et cliquer sur **Enregistrer et calculer**. Les notes sont facultatives, limitées à 2 000 caractères par question et conservées localement. Après relance, ouvrir le même projet et revenir au questionnaire pour retrouver réponses, notes et résultats.

L'échelle va de 0 (absent) à 3 (systématisé), avec « Inconnu » par défaut. Le score porte uniquement sur les réponses connues ; la couverture indique combien des trois questions de la dimension sont renseignées. Exemple : `3, 0, inconnu` donne un score de **50/100** avec une couverture de **2/3**. Sans réponse connue, le score est **Indéterminé**, jamais zéro. Un score peu couvert est signalé comme insuffisant pour interpréter la dimension.

Les résultats affichés sont ceux de la dernière sauvegarde. Une modification non enregistrée ne change pas ces résultats. Un nouvel enregistrement remplace le questionnaire courant du projet et préserve les diagnostics déjà finalisés. Le questionnaire `calms-v1` et ses interprétations sont des conventions internes proposées, sans score global ni certification officielle.

## Finaliser un diagnostic

Dans **Diagnostic guidé**, après CALMS et/ou le scan, choisir les sources à inclure puis cliquer sur **Finaliser et sauvegarder**. Seules les réponses déjà enregistrées sont utilisées. Décocher une source permet de la passer ; passer les deux est refusé. Un questionnaire tout inconnu reste une source admissible, avec cinq scores indéterminés et uniquement des demandes de complément. Un scan refusé ne devient jamais une preuve de configuration manquante.

Le diagnostic enregistre les réponses normalisées, scores, onze constats, versions, rapprochements et recommandations. Les notes restent dans le brouillon local ; elles ne sont pas copiées dans le snapshot. Chaque fiche montre source, statut, preuve contrôlée, limite, gravité éventuelle et lien vers l'action. Les filtres de statut/source changent l'affichage uniquement. Les écarts déclaratifs/indices donnent une explication prudente, sans malus CALMS ni contradiction affirmée.

Les actions sont classées par impact/effort décroissant puis identifiant croissant : privilèges explicitement déclarés (3), vérifications Docker ciblées sur un risque (2), amélioration CALMS suffisamment couverte (1) et information à confirmer (1). Une information inconnue n'a pas de gravité technique établie. Aucune dépendance automatique n'est ajoutée. Ces conventions pédagogiques restent proposées.

Une double pression avec les mêmes sources dans la même session réutilise la clé de soumission. **Préparer une nouvelle soumission** permet de créer volontairement un autre diagnostic avec les mêmes données ; changer les données puis finaliser crée aussi une nouvelle soumission. Le dernier diagnostic est relu après redémarrage sans rescanner ni recalculer. Une nouvelle session de finalisation constitue une nouvelle soumission volontaire. Les diagnostics précédents sont sélectionnables dans Plan d’action et Historique / Rapports ; leur suivi reste distinct.

**Vérification reproductible des diagnostics (exercée en P6) :** finaliser CALMS seul, scan seul puis combiné ; vérifier les fiches et filtres, le lien déclaration/indices avec A2=3 et `incomplete_project`, la priorité CMP-001 en tête, la double pression sans doublon, puis relancer et retrouver le dernier diagnostic. Décocher les deux sources doit refuser la finalisation. Le parcours Chrome et les assertions AppTest sont documentés séparément dans TEST_RESULTS.

## Suivre les actions et exporter

Dans **Plan d’action**, sélectionner le diagnostic enregistré. Les recommandations commencent dans l’ordre de priorité initial ; **Monter** et **Descendre** modifient l’ordre de suivi sans changer cette priorité. Choisir **À faire**, **En cours**, **Terminée** ou **Écartée**, saisir un commentaire facultatif (2 000 caractères maximum), puis **Enregistrer le suivi**. Enregistrer le formulaire avant de déplacer une action. Les dates de création sont conservées, les dates de mise à jour suivent les modifications réelles. Le suivi ne change jamais les scores CALMS ni les recommandations figées ; deux diagnostics du même projet ont des actions distinctes.

Dans **Historique / Rapports**, sélectionner un ancien diagnostic pour consulter ses résultats et versions sans relancer le scanner ou le moteur. Les aperçus Markdown/JSON et les deux boutons **Télécharger** partagent un seul objet `report-v1`, daté en UTC. Les actions y suivent l’ordre choisi ; les recommandations gardent leur priorité d’origine. Un changement du suivi actualise l’objet automatiquement ; **Actualiser la date du rapport** renouvelle explicitement sa date. Une ancienne version de snapshot inconnue est signalée sans empêcher de sélectionner un autre diagnostic lisible.

Les rapports comprennent les identifiants projet/diagnostic, dates, versions, réponses numériques/inconnues, scores et couvertures, constats/preuves contrôlées, rapprochements, limites, recommandations et état des actions. Les noms/descriptions de projet, notes CALMS, commentaires, noms d’archives, chemins, noms libres de jobs/services et contenus bruts sont exclus. Les commentaires restent disponibles dans le formulaire local. Les noms des fichiers téléchargés n’utilisent que les identifiants.

**Recette manuelle P5 reproductible :** créer deux diagnostics du même projet, ouvrir Plan d’action, essayer les quatre statuts et un commentaire, changer l’ordre, relancer puis rouvrir le projet. Vérifier que le suivi est conservé, les scores inchangés et l’autre diagnostic indépendant. Dans Historique / Rapports, rouvrir chaque résultat puis télécharger les deux formats : même date, même ordre et statuts, accents lisibles, aucun texte libre exclu. La vérification navigateur effectivement observée et ses limites sont consignées dans TEST_RESULTS.

## Analyser des configurations

Dans **Diagnostic guidé**, descendre à **Scan technique ciblé**, choisir **Archive ZIP** ou **Dossier local**, fournir la source puis cliquer sur **Analyser le dépôt**. Le questionnaire peut rester non renseigné. Sous Linux, les deux fixtures de `tests/fixtures/` peuvent être analysées directement par leur chemin ; elles sont fictives et ne doivent pas être exécutées.

Seuls `Dockerfile`, `compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml` et `.gitlab-ci.yml` sont lus à la racine supportée. Le ZIP peut avoir un dossier enveloppe unique. Les autres contenus, dont `.env`, clés et `.git/`, ne sont pas ouverts ; aucun ZIP n'est extrait. Le dossier local n'est pas parcouru récursivement. Aucun code, build, script ou include distant n'est exécuté.

Bornes : ZIP reçu ≤10 Mio et ≤1 000 entrées, noms ≤1 024 octets UTF-8 (slash final compris) et ≤32 composants (slash final exclu du comptage), tailles annoncées ≤20 Mio cumulés et ≤1 Mio par fichier, ratio de compression ≤100, lecture réelle ≤1 Mio par configuration et ≤6 Mio au total. Les noms et métadonnées de toutes les entrées ZIP sont contrôlés, même pour les fichiers ignorés, avant toute ouverture de contenu. Les liens, chemins suspects, doublons, entrées chiffrées/spéciales et budgets dépassés rejettent l'analyse entière. Le scan local refuse les liens et requiert les primitives de protection essayées sous Linux ; il est refusé lorsqu'elles sont indisponibles. Windows/macOS ne sont pas vérifiés pour ce scan.

Les six contrôles recherchent : Dockerfile, tag `latest` explicite, instruction `USER` du dernier stage, booléen Compose `privileged: true`, fichier GitLab CI et job local de test identifiable. Les statuts distinguent indice observé, point à vérifier, non observé, à confirmer, non analysable et non applicable. Un fichier absent de ce périmètre ne prouve pas l'absence d'une pratique. GitLab CI reste provisoire ; aucune conclusion sur les autres fournisseurs.

Le parsing est limité : pas de validation complète Docker/Compose/GitLab, pas de résolution d'héritage/includes/fusion. Docker : commentaires, continuations antislash et stages pris en charge ; heredocs et échappement backtick restent à confirmer. YAML : UTF-8, profondeur ≤32, ≤10 000 nœuds, clés uniques textuelles ; tous les alias sont refusés en V1, même non récursifs. `privileged: "true"` est non analysable ; seul un booléen est interprété (résolution YAML 1.1 de PyYAML). Un `privileged: true` explicite reste signalé si un autre service est invalide, avec preuve d'analyse partielle ; cela n'établit pas le fonctionnement réel du service. Les variantes Compose restent indépendantes.

Le résultat `scanner-v2`/`rules-v2` est temporaire et isolé par projet dans la session jusqu'à finalisation explicite du diagnostic. La navigation conserve le mode, le chemin local et le résultat ; la source du dernier scan est affichée. Après retour à la page, un ZIP doit être sélectionné à nouveau pour relancer son analyse ; le dernier résultat reste finalisable. Changer de source/chemin/archive ou relancer l'analyse efface le résultat avant traitement ; une entrée rejetée ne laisse pas l'ancien résultat temporaire affiché. Refaire le scan si les fichiers ont changé. Les diagnostics déjà finalisés, y compris les versions v1, restent conservés et relus sans recalcul ; les exports portent sur les résultats finalisés et le suivi courant.

**Vérification reproductible du scan (exercée en P6) :** démarrer localement avec la commande ci-dessous, créer/ouvrir un projet, analyser `tests/fixtures/healthy_project`, puis `incomplete_project`. Attendus : six indices observés pour le premier ; `latest`, USER non explicite dans le dernier stage et privilèges à vérifier, job de test à confirmer pour le second. Importer ensuite ces mêmes fichiers dans un ZIP direct et dans un dossier enveloppe ; vérifier les mêmes constats. Essayer un ZIP invalide : message neutre et disparition du résultat précédent. AppTest exerce le service et les widgets simulés ; en P6, Chrome a réellement envoyé les ZIP direct/enveloppe et une archive hostile rejetée.

## Installation Linux

Ouvrir un terminal **à la racine de ce dossier**, avec Python >= 3.11 et un accès au dépôt de paquets pour la première installation :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m streamlit run app.py --server.address 127.0.0.1
```

Puis ouvrir `http://127.0.0.1:8501` dans un navigateur. Arrêter avec `Ctrl+C`. À la prochaine utilisation, activer le même environnement et relancer la dernière commande ; aucune réinstallation n'est nécessaire tant que les dépendances ne changent pas.

Une installation neuve dans un environnement virtuel isolé, depuis une copie propre des sources, a réussi sous Linux avec Python 3.13.5, Streamlit 1.66.0, pytest 9.1.1 et PyYAML 6.0.3. La suite y donne **380 tests et 6 sous-tests réussis** ; `python -m pip check` réussit. Le téléchargement des dépendances a nécessité une autorisation réseau hors sandbox. macOS n'a pas été essayé ; le scan local refuse les plateformes sans les primitives de protection requises.

## Installation Windows — PowerShell

Ces commandes sont fournies pour un poste équipé de Python >= 3.11 ; **Windows n'a pas été testé**. Elles utilisent directement l'interpréteur de l'environnement virtuel, sans modifier la politique d'exécution PowerShell :

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Ouvrir ensuite `http://127.0.0.1:8501`. Après un arrêt, relancer la dernière commande à la racine du dossier.

## Données et configuration

La base est créée au premier lancement dans `var/devops_assist.sqlite3`, sous la racine de l'application. Elle est exclue du versionnement. Après redémarrage, ouvrir le projet depuis la liste ; la sélection active dans la GUI est propre à la session.

La GUI lit uniquement les configurations ciblées lors d'une analyse demandée et n'exécute aucun dépôt. Les saisies libres sont affichées comme du texte, sans interprétation Markdown/HTML. Ne pas enregistrer de secrets dans le contexte du projet. Les preuves du scan ne contiennent que noms autorisés, codes et numéros de ligne fiables éventuels.

Le fichier [.streamlit/config.toml](.streamlit/config.toml) fixe l'adresse locale `127.0.0.1`, désactive la télémétrie et le service de fichiers statiques. Lancer depuis la racine pour charger cette configuration. La commande de lancement précise aussi l'adresse locale.

Pour les tests ou une base locale distincte, la variable `DEVOPS_ASSIST_DB_PATH` permet de choisir un autre fichier. Sans cette variable, le chemin ci-dessus est utilisé. La configuration n'exige aucun compte ni service distant.

## Tests et vérification

La suite complète est `python -m pytest -q` : **423 tests et 6 sous-tests réussis**, dont 49 cas GUI AppTest. Elle couvre projets, migration de l'ancienne base, questionnaire, scanner/règles et attaques, constats, priorités, transaction diagnostic/actions, soumissions concurrentes, reprise sans recalcul, suivi/ordre transactionnel, isolation des diagnostics et sécurité/cohérence des rapports. Les 43 cas ajoutés après audit couvrent aussi la navigation du scan, les services Compose partiellement invalides, les bornes et conflits des chemins ZIP et la relecture des versions historiques. Les preuves sont dans [TEST_RESULTS](context/TEST_RESULTS.md). Les tests utilisent des bases et sources temporaires ; la base utilisateur n'est pas utilisée. La recette Chrome P6 complète a réussi le 2026-10-20 depuis l'installation neuve, avec deux redémarrages réels et des transferts de fichiers effectifs. Le 2026-10-30, cinq contrôles Chrome ciblés ont vérifié les corrections avec de vrais uploads ZIP et relecture SQLite. Les pourcentages de couverture CALMS ont été corrigés pendant P6 (3/3 = 100 %, 2/3 = 66,7 %), sans changer les scores ni les données enregistrées.

Les tests SQLite peuvent aussi s'exécuter sans pytest ni Streamlit. Cette commande a effectivement réussi après installation locale du package sans ses dépendances :

```bash
python -m unittest discover -s tests -p test_db.py -v
```

En cas d'environnement hors réseau, cette dernière commande ne remplace pas les tests GUI. Les résultats actuels, les anciennes erreurs d'installation et les étapes de recette réellement observées sont détaillés dans [TEST_RESULTS](context/TEST_RESULTS.md).

## Organisation et suite

- [PRODUCT](context/PRODUCT.md) : périmètre V1 et limites ; [ARCHITECTURE](context/ARCHITECTURE.md) : responsabilités et arborescence cible.
- [PLAN](context/PLAN.md) : jalons ; [STATE](context/STATE.md) : état réel ; [JOURNAL](context/JOURNAL.md) : changements et vérifications.
- [DECISIONS](context/DECISIONS.md) : statuts et questions ouvertes ; [RULES](context/RULES.md) : règles proposées ; [ACCEPTANCE](context/ACCEPTANCE.md) : critères du MVP complet.

Python, Streamlit et SQLite sont utilisés pour cette première implémentation, suivant la proposition DEC-009 ; cela ne constitue pas une validation historique rétroactive. GitLab CI, 15 questions, six contrôles techniques et les conventions de scoring restent proposés. Les questions CI, OS de référence définitif, barème et évaluation académique restent ouvertes.

P0–P6 sont achevés pour le MVP local sous Linux. Les trois défauts majeurs de l'[audit final](context/AUDIT.md) sont corrigés et couverts depuis le 2026-10-30 : source cohérente après navigation, signal Compose préservé parmi des services invalides et chemins ZIP bornés avec contrôle des conflits par composants. La prochaine étape dépend des besoins de l'utilisateur : préparer la démonstration et l'évaluation académique (justification du barème, témoins, protocole). Ces corrections ciblées ne valent pas nouvel audit exhaustif. Aucune extension fonctionnelle ni publication n'est lancée automatiquement.

## Sources historiques

Les documents [Contexte vivant](https://docs.google.com/document/d/1WxKroy2ryE5tyHwbU93iiMuh-vORcDkUHvFAukxTQhM/edit) et [Spécification GUI v0.2](https://docs.google.com/document/d/1_6fEgdTB8PgDRCAP-EDtj8N5jMUM9aOFqXR6q3rNQ5M/edit) sont les sources Drive référencées par le kit initial. Aucune synchronisation automatique n'est réalisée. Les copies de `references/` restent en lecture seule.
