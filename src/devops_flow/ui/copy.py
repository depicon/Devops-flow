"""Vocabulaire de présentation ; les codes métier et les snapshots restent intacts."""
from types import MappingProxyType

DIMENSION_LABELS = MappingProxyType({
    'C': 'Culture', 'A': 'Automatisation', 'L': 'Amélioration du travail',
    'M': 'Mesure', 'S': 'Partage des connaissances',
})
RULE_TITLES = MappingProxyType({
    'DOC-001': 'Présence d’un Dockerfile',
    'DOC-002': 'Utilisation explicite du tag latest',
    'DOC-003': 'Utilisateur déclaré dans l’image finale',
    'CMP-001': 'Privilèges élevés dans Compose',
    'CI-001': 'Présence d’une configuration GitLab CI',
    'CI-002': 'Étape de tests identifiable dans GitLab CI',
    **{'CAL-' + key: value for key, value in DIMENSION_LABELS.items()},
})
RULE_HELP = MappingProxyType({
    'DOC-001': ('Repérer le fichier de construction de l’image.', 'Le fichier est recherché dans le périmètre du composant. Sa présence ne prouve pas qu’une image est construite.'),
    'DOC-002': ('Repérer une référence d’image utilisant explicitement :latest.', 'Un autre tag ne garantit pas une image immuable. Les images déterminées par variable restent à confirmer.'),
    'DOC-003': ('Vérifier si USER est déclaré dans la dernière étape du Dockerfile.', 'L’utilisateur hérité de l’image et les paramètres réels de lancement ne sont pas connus.'),
    'CMP-001': ('Repérer privileged: true dans les services Compose.', 'Les extensions non résolues et les configurations non analysables limitent la conclusion.'),
    'CI-001': ('Repérer le fichier .gitlab-ci.yml.', 'Les autres fournisseurs et emplacements ne sont pas évalués par ce contrôle.'),
    'CI-002': ('Repérer une tâche locale avec script et une étape ou un nom de tests reconnu.', 'Les commandes ne sont pas exécutées. La présence d’une tâche ne prouve pas la réussite des tests.'),
})
PROOF_LABELS = MappingProxyType({
    'file_found': 'Fichier trouvé', 'explicit_latest': 'Tag latest explicite',
    'image_variable': 'Image déterminée par une variable non résolue',
    'no_explicit_latest': 'Aucun tag latest explicite repéré',
    'final_user': 'Instruction USER dans la dernière étape',
    'no_final_user': 'Aucune instruction USER dans la dernière étape',
    'docker_unknown': 'Construction Docker hors du sous-ensemble analysé',
    'docker_invalid': 'Dockerfile non analysable',
    'privileged_true': 'Privilèges élevés explicitement activés',
    'no_privileged_true': 'Aucun privilège élevé explicite repéré',
    'compose_unresolved': 'Configuration Compose partiellement résolue',
    'invalid_compose': 'Configuration Compose non analysable',
    'local_test_job': 'Tâche locale de tests repérée',
    'test_job_unconfirmed': 'Tâche de tests à confirmer', 'invalid_ci': 'Configuration GitLab CI non analysable',
    'local_build_job': 'Tâche locale de construction repérée', 'build_job_unconfirmed': 'Tâche de construction à confirmer',
    'local_deploy_job': 'Tâche locale de déploiement repérée', 'deploy_job_unconfirmed': 'Tâche de déploiement à confirmer',
    **{'service_type_' + name: 'Type de service déclaré : ' + label for name, label in {
        'postgres': 'PostgreSQL', 'redis': 'Redis', 'mysql': 'MySQL', 'rabbitmq': 'RabbitMQ',
        'cache': 'cache', 'database': 'base de données', 'queue': 'file de messages',
        'unresolved': 'à confirmer',
    }.items()},
})
ANSWER_LABELS = { 'unknown': 'Je ne sais pas', 0: 'Jamais', 1: 'Ponctuellement',
                  2: 'Partiellement ou régulièrement', 3: 'Systématiquement' }


def rule_title(rule_id):
    return RULE_TITLES.get(rule_id, 'Contrôle complémentaire')


def dimension_label(dimension_id, original):
    return DIMENSION_LABELS.get(dimension_id, original)


def evidence_label(evidence):
    if evidence.get('question_id'):
        return 'Réponse à la question ' + evidence['question_id'] + ' : ' + ANSWER_LABELS.get(evidence['value'], str(evidence['value']))
    description = PROOF_LABELS.get(evidence['code'], 'Indice complémentaire')
    return evidence['file'] + ' · ' + description + (f" · ligne {evidence['line']}" if evidence.get('line') else '')


def finding_title(finding_id, findings=None):
    if findings is not None:
        match = next((f for f in findings if f['id'] == finding_id), None)
        if match:
            return rule_title(match['rule_id'])
    return rule_title(finding_id.removeprefix('F-'))
