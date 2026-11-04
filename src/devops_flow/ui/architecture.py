"""Architecture et déclarations : présentation seule, champs libres en labels."""
from nicegui import run, ui
import math
from devops_flow.architecture import ProjectArchitecture
from devops_flow.ui.components import button, kpi_card, marked, section, text

CONFIDENCE_LABELS = {'high': 'Déclaration explicite', 'medium': 'Convention ou relation à confirmer',
    'low': 'Hypothèse faible — à confirmer', 'manual': 'Déclaré manuellement', 'unknown': 'À confirmer'}
FIELD_LABELS = {'language': 'Langage', 'runtime': 'Version / contrainte runtime', 'framework': 'Framework déclaré',
    'entrypoint': 'Point d’entrée candidat', 'port': 'Port', 'environment': 'Nom de variable',
    'service': 'Type de service déclaré', 'build_command': 'Commande de construction', 'test_command': 'Commande de tests'}
WARNING_LABELS = {'invalid_manifest': 'Un manifest est non analysable ; ses informations restent à confirmer.',
    'unresolved_manifest': 'Certaines déclarations dynamiques ne sont pas résolues.',
    'inactive_override': 'Une déclaration concerne un chemin non retrouvé dans ce scan.',
    'conflicting_signals': 'Plusieurs versions ou ports sont déclarés : vérifiez leur portée.',
    'entrypoint_truncated': 'La lecture des points d’entrée est limitée aux 200 premières lignes et à 64 Kio.'}


def scope_labels(data):
    return {'project': 'Projet / configurations communes', **{c['id']: '(racine)' if c['path'] == '.' else c['path']
            for c in data.get('architecture', {}).get('components', [])}}


def render_architecture(data, key, *, findings=(), on_open_finding=None, current=False):
    with section('Architecture DevOps', 'Vue issue de déclarations et d’indices statiques ; usage effectif à confirmer.'):
        text('Analyse courante de cette session.' if current else 'Architecture du diagnostic sauvegardé ; aucune nouvelle analyse.', caption=True)
        if data is None:
            text('Architecture non enregistrée pour ce diagnostic ou cette source. Les constats existants restent consultables.', caption=True)
            return
        architecture = ProjectArchitecture.from_dict(data)
        with ui.row().classes('w-full gap-4'):
            kpi_card('Composants', len(architecture.components))
            kpi_card('Relations déclarées', len(architecture.relations))
            kpi_card('Fichiers non assignés', len(architecture.unassigned_files))
        if not architecture.components:
            text('Aucun composant identifié par manifest. Les configurations non assignées restent contrôlées au niveau projet. Vous pouvez déclarer une stack puis relancer l’analyse.')
        for warning in architecture.warnings:
            text(WARNING_LABELS[warning], caption=True).classes('feedback').props('role=status')
        for component in architecture.components:
            with marked(ui.card().classes('app-card'), f'architecture_component_{key}_{component.id}'):
                text('(racine)' if component.path == '.' else component.path).classes('section-heading')
                text('Composant déclaré manuellement' if component.declared else 'Composant repéré par manifest — périmètre à confirmer', caption=True)
                effective = component.effective_signals
                with ui.row().classes('w-full gap-3 flex-wrap'):
                    for signal in effective:
                        if signal.field in {'language', 'runtime', 'framework'}:
                            text(f'{FIELD_LABELS[signal.field]} : {signal.value or "à confirmer"} · {CONFIDENCE_LABELS[signal.confidence]}').classes('profile-badge')
                with ui.expansion('Signaux, sources et commandes proposées'):
                    for signal in component.signals:
                        text(f'{FIELD_LABELS[signal.field]} : {signal.value if signal.value is not None else "à confirmer"}')
                        text(CONFIDENCE_LABELS[signal.confidence] + (f' · {signal.file}' if signal.file else '') +
                             (f' · ligne {signal.line}' if signal.line else ''), caption=True)
                    text('Les commandes ne sont jamais exécutées ; les dépendances déclarées ne prouvent pas leur usage.', caption=True)
                with ui.expansion('Fichiers DevOps et vérifications potentielles', value=True):
                    text('Dans le périmètre : ' + (', '.join(component.devops.files) or 'aucun fichier ciblé observé'))
                    text('Références communes : ' + (', '.join(component.devops.linked_files) or 'aucune attribution explicite'), caption=True)
                    if component.devops.potential_missing:
                        text('Non observé — besoin à confirmer : ' + ', '.join(component.devops.potential_missing), caption=True)
                    if on_open_finding:
                        for finding in findings:
                            if finding.get('scope_id') == component.id:
                                from devops_flow.ui.copy import rule_title
                                button('Voir : ' + rule_title(finding['rule_id']), lambda fid=finding['id']: on_open_finding(fid),
                                       f"architecture_finding_{key}_{finding['id']}")
        if architecture.shared_files or architecture.unassigned_files:
            with ui.expansion('Configurations communes ou non assignées'):
                text('Communes : ' + (', '.join(architecture.shared_files) or 'aucune référence explicite'))
                text('Non assignées : ' + (', '.join(architecture.unassigned_files) or 'aucune'))
        if architecture.relations:
            names = {'project': 'Projet', **{c.id: '(racine)' if c.path == '.' else c.path for c in architecture.components}}
            with ui.expansion('Relations de configuration déclarées'):
                for relation in architecture.relations:
                    label = {'compose_build': 'contexte Compose', 'ci_include': 'inclusion GitLab locale', 'compose_dependency': 'dépendance Compose déclarée'}[relation.kind]
                    text(f'{names[relation.source]} → {names[relation.target]} · {label} · {relation.file}')


def render_declarations(shell, project_id):
    state = shell.session.project(project_id)
    revision, declarations = shell.session.store.load_stack_overrides(project_id)
    with ui.expansion('Déclarer ou corriger une stack'):
        text('Déclarations enregistrées pour ce projet. Une modification impose une nouvelle analyse et ne change aucun diagnostic sauvegardé.', caption=True)
        choices = {'__new__': 'Nouvelle déclaration', **{p: '(racine)' if p == '.' else p for p in declarations}}
        selector = (marked(ui.select(choices, value=state.override_editor if state.override_editor in choices else '__new__', label='Déclaration à modifier').props('outlined').classes('w-full'), f'override_select_{project_id}')
                    if declarations else None)
        @ui.refreshable
        def form():
            selected = selector.value if selector else '__new__'
            previous = declarations.get(selected, {})
            draft = state.override_drafts.setdefault(selected, dict(previous, path=selected if selected != '__new__' else '.', _revision=revision))
            path = marked(ui.input('Chemin relatif du composant (. pour la racine)').bind_value(draft, 'path').props('outlined').classes('w-full'), f'override_path_{project_id}')
            if selected != '__new__': path.disable()
            for field in ('language', 'runtime', 'framework', 'entrypoint'): draft.setdefault(field, '')
            draft.setdefault('port', None)
            language = marked(ui.input('Langage (facultatif)').bind_value(draft, 'language').props('outlined').classes('w-full'), f'override_language_{project_id}')
            runtime = marked(ui.input('Version / contrainte runtime (facultative)').bind_value(draft, 'runtime').props('outlined').classes('w-full'), f'override_runtime_{project_id}')
            framework = marked(ui.input('Framework (facultatif)').bind_value(draft, 'framework').props('outlined').classes('w-full'), f'override_framework_{project_id}')
            entry = marked(ui.input('Point d’entrée relatif à la racine de scan (facultatif)').bind_value(draft, 'entrypoint').props('outlined').classes('w-full'), f'override_entrypoint_{project_id}')
            port = marked(ui.number('Port (facultatif)', min=1, max=65535, precision=0).bind_value(draft, 'port').props('outlined').classes('w-full'), f'override_port_{project_id}')
            async def save():
                values = {k: field.value.strip() for k, field in [('language', language), ('runtime', runtime), ('framework', framework), ('entrypoint', entry)] if field.value and field.value.strip()}
                if port.value is not None:
                    if not math.isfinite(port.value) or port.value != int(port.value): raise ValueError('Le port doit être entier.')
                    values['port'] = int(port.value)
                await run.io_bound(shell.session.store.save_stack_override, project_id, path.value.strip(), values, expected_revision=draft['_revision'])
                state.invalidate()
                state.new_submission()
                state.override_drafts.clear()
                state.override_editor = '__new__'
            button('Enregistrer la déclaration', shell.action(save, 'Déclaration enregistrée. Relancez l’analyse ; pour un ZIP, sélectionnez à nouveau l’archive.'), f'override_save_{project_id}', primary=True)
            if selected != '__new__':
                async def delete():
                    await run.io_bound(shell.session.store.delete_stack_override, project_id, selected, expected_revision=draft['_revision'])
                    state.invalidate()
                    state.new_submission()
                    state.override_drafts.clear()
                    state.override_editor = '__new__'
                button('Supprimer cette déclaration', shell.action(delete, 'Déclaration supprimée. Relancez l’analyse.'), f'override_delete_{project_id}')
            text('Le chemin doit être retrouvé dans le prochain scan pour devenir un composant actif. Les signaux automatiques restent conservés.', caption=True)
        form()
        def select(event):
            state.override_editor = event.value
            form.refresh()
        if selector:
            selector.on_value_change(select)
        def reload():
            state.override_drafts.clear()
            state.override_editor = '__new__'
        button('Recharger les déclarations', shell.action(reload, 'Déclarations rechargées ; les saisies non enregistrées ont été retirées.'), f'override_reload_{project_id}')
