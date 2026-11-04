"""Résultats figés et finalisation ; pas de SQL dans la présentation."""
import json
from nicegui import ui
from devops_flow.diagnostic import DiagnosticSnapshot
from devops_flow.ui.components import FINDING_LABELS, button, finding_card, marked, navigation_button, profile_badge, recommendation_card, score_badge, section, text
from devops_flow.ui.flow import render_flow
from devops_flow.ui.copy import finding_title
from devops_flow.ui.architecture import render_architecture, scope_labels


def render_results(shell, project_id):
    state = shell.session.project(project_id)
    with ui.element('div').props('id=finaliser-le-diagnostic').classes('w-full'), section('Créer le diagnostic', 'Vérifiez les sources à utiliser. Le résultat sauvegardé conservera ces données.'):
        profile = shell.session.store.project_profile(project_id)
        profile_badge(profile)
        text('Profil utilisé pour ce diagnostic. Modifiez-le dans les réglages du projet.', caption=True)
        text('Seules les réponses enregistrées et la dernière analyse disponible sont utilisés. Les modifications non enregistrées sont exclues.', caption=True)
        try:
            saved = shell.session.store.load_calms(project_id, profile=profile)
        except ValueError as error:
            saved = None
            text(str(error)).classes('feedback error')
        if state.include_calms is None and saved is not None:
            state.include_calms = True
        if state.include_scan is None and state.scan is not None:
            state.include_scan = True
        text('Sources disponibles : questionnaire ' + ('enregistré' if saved else 'non enregistré') + ' · analyse du dépôt ' + ('disponible' if state.scan else 'non disponible'), caption=True)
        questionnaire = marked(ui.checkbox('Inclure le questionnaire enregistré', value=saved is not None if state.include_calms is None else state.include_calms), f'include_calms_{project_id}')
        repository = marked(ui.checkbox('Inclure l’analyse du dépôt de cette session', value=state.scan is not None if state.include_scan is None else state.include_scan), f'include_scan_{project_id}')
        async def finalize():
            state.include_calms, state.include_scan = questionnaire.value, repository.value
            await shell.session.finalize(project_id)
        create = button('Créer le diagnostic', shell.action(finalize,
               'Diagnostic sauvegardé avec ses actions. Consultez les résultats puis choisissez les améliorations à suivre.'), f'diagnostic_finalize_{project_id}', primary=True)
        readiness = text('', caption=True, key=f'diagnostic_readiness_{project_id}').props('role=status')
        def reason():
            if (questionnaire.value and saved is None) or (repository.value and state.scan is None):
                return 'Une source sélectionnée est indisponible : enregistrez le questionnaire, analysez le dépôt ou décochez cette source.'
            if not questionnaire.value and not repository.value:
                return 'Enregistrez un questionnaire ou réalisez un scan du dépôt, puis sélectionnez au moins une source.'
            return 'Les sources sélectionnées sont prêtes. Vous pouvez créer le diagnostic.'
        def available():
            return bool((questionnaire.value or repository.value) and not
                        ((questionnaire.value and saved is None) or (repository.value and state.scan is None)))
        def ready():
            create.set_enabled(available())
            readiness.set_text(reason())
        def selected():
            nonlocal saved
            try:
                saved = shell.session.store.load_calms(project_id, profile=profile)
            except ValueError:
                saved = None
            state.include_calms, state.include_scan = questionnaire.value, repository.value
            ready()
        questionnaire.on_value_change(selected)
        repository.on_value_change(selected)
        ready()
        create.bind_enabled_from(state, 'revision', backward=lambda _: available())
        readiness.bind_text_from(state, 'revision', backward=lambda _: reason())
        if state.submitted:
            def new():
                state.new_submission()
                shell.refresh()
            button('Créer un autre diagnostic avec ces sources', new, f'diagnostic_new_{project_id}')
    items = shell.session.store.list_diagnostics(project_id)
    if not items:
        text('Aucun diagnostic sauvegardé. Un questionnaire enregistré ou une analyse du dépôt suffit ; vous pouvez combiner les deux.', caption=True)
        return
    options = {d.id: f"Diagnostic du {d.created_at.strftime('%d/%m/%Y à %H:%M:%S')} UTC · n° {len(items) - index}" for index, d in enumerate(items)}
    selected_id = state.selected.get('Diagnostic guidé', items[0].id)
    if selected_id not in options:
        selected_id = items[0].id
    state.selected['Diagnostic guidé'] = selected_id
    with section('Résultats du diagnostic sauvegardé'):
        def choose(event):
            state.selected['Diagnostic guidé'] = event.value
            state.focused_finding = None
            shell.refresh()
        marked(ui.select(options, value=selected_id, label='Diagnostic consulté', on_change=choose)
               .props('outlined').classes('w-full'), f'results_diagnostic_{project_id}')
        try:
            latest = shell.session.store.get_diagnostic(project_id, selected_id)
        except ValueError as error:
            text(str(error), key='data_error').classes('feedback error').props('role=alert')
            return
        if latest is None:
            text('Ce diagnostic n’est plus disponible. Sélectionnez un autre résultat.').classes('feedback error')
            return
        text(f"Sauvegardé le {latest.created_at.strftime('%d/%m/%Y à %H:%M')} UTC. Les modifications des sources ne changent pas ce résultat.", caption=True)
        with ui.expansion('Référence du diagnostic'):
            text(latest.id, caption=True)
        with ui.row():
            navigation_button(shell, 'Suivre les actions', "Plan d'action", f'results_roadmap_{project_id}', diagnostic_id=latest.id, primary=True)
            navigation_button(shell, 'Consulter les rapports', 'Historique / Rapports', f'results_history_{project_id}', diagnostic_id=latest.id)
        render_snapshot(latest.snapshot.to_dict(), project_id, focus_finding=state.focused_finding)


def render_snapshot(data, key, *, on_open_finding=None, focus_finding=None):
    names = scope_labels(data)
    profile_badge(data.get('profile', {'slug':'baseline','version':1}))
    if data['scores']:
        with ui.row().classes('w-full gap-4'):
            for score in data['scores']:
                score_badge(score)
    else:
        text('Sans questionnaire inclus : aucun indicateur sur les pratiques de l’équipe.', caption=True)
    for alignment in data['alignments']:
        text(alignment['explanation']).classes('feedback')
        text(alignment['limitation'], caption=True)
        text('Constats concernés : ' + ', '.join(finding_title(fid, data['findings']) for fid in alignment['source_finding_ids']), caption=True)
    flow_box = ui.column().classes('w-full')
    focused = [focus_finding]
    cards = {}
    with ui.tabs().classes('w-full').props('align=left outside-arrows mobile-arrows') as tabs:
        findings_tab = ui.tab('findings', label=f"Constats ({len(data['findings'])})")
        actions_tab = ui.tab('recommendations', label=f"Recommandations ({len(data['recommendations'])})")
        architecture_tab = marked(ui.tab('architecture', label='Architecture'), f'architecture_tab_{key}')
    with ui.tab_panels(tabs, value=findings_tab).classes('w-full'):
        with ui.tab_panel(findings_tab).classes('p-0 flex flex-col gap-4'):
            with ui.row().classes('w-full'):
                status = marked(ui.select({'Tous': 'Tous', **FINDING_LABELS}, value='Tous', label='Filtrer les constats par statut').props('outlined').classes('grow'), f'findings_status_{key}')
                source = marked(ui.select(['Toutes', 'déclaré', 'observé', 'inconnu'], value='Toutes', label='Filtrer par source').props('outlined').classes('grow'), f'findings_source_{key}')
                scope = marked(ui.select({'Tous': 'Tous les périmètres', **names}, value='Tous', label='Filtrer par composant').props('outlined').classes('grow'), f'findings_scope_{key}') if data.get('architecture') else None
            @ui.refreshable
            def findings():
                cards.clear()
                visible = [f for f in data['findings'] if (status.value == 'Tous' or f['status'] == status.value)
                           and (source.value == 'Toutes' or f['source'] == source.value)
                           and (scope is None or scope.value == 'Tous' or f.get('scope_id') == scope.value)]
                text(f"{len(visible)} / {len(data['findings'])} constats affichés", caption=True)
                if not visible:
                    text('Aucun constat pour ces filtres. Sélectionnez Tous et Toutes pour retrouver l’ensemble.')
                questions = {variant: q['text'] for entries in data.get('profile', {}).get('calms', {}).get('questions', {}).values()
                             for q in entries for variant in (q['id'], q['id'].upper())}
                for finding in visible:
                    if len(names) > 1:
                        text('Périmètre : ' + names[finding['scope_id']], caption=True)
                    card = marked(finding_card(finding, recommendations=data['recommendations'], questions=questions), f"finding_{key}_{finding['id']}")
                    cards[finding['id']] = card
                    if finding['id'] == focused[0]:
                        card.open()
            findings()
            status.on_value_change(lambda: findings.refresh())
            source.on_value_change(lambda: findings.refresh())
            if scope is not None:
                scope.on_value_change(lambda: findings.refresh())
        with ui.tab_panel(actions_tab).classes('p-0 flex flex-col gap-4'):
            text('Actions proposées par priorité', caption=True)
            if not data['recommendations']:
                text('Aucune action automatique pour les indices et déclarations retenus. Cela ne certifie pas les pratiques.')
            for recommendation in data['recommendations']:
                if len(names) > 1:
                    text('Périmètre : ' + names[recommendation['scope_id']], caption=True)
                recommendation_card(recommendation)
        with ui.tab_panel(architecture_tab).classes('p-0'):
            render_architecture(data.get('architecture'), key, findings=data['findings'],
                                on_open_finding=on_open_finding or (lambda fid: show_finding(fid)))

    def show_finding(finding_id):
        if finding_id not in {f['id'] for f in data['findings']}:
            return
        focused[0] = finding_id
        tabs.value = findings_tab
        status.value = 'Tous'
        source.value = 'Toutes'
        if scope is not None:
            scope.value = 'Tous'
        findings.refresh()
        card = cards.get(finding_id)
        if card:
            ui.run_javascript(f'document.getElementById("c{card.id}")?.scrollIntoView({{block:"center",behavior:"smooth"}})')

    with flow_box:
        render_flow(DiagnosticSnapshot(json.dumps(data, ensure_ascii=False)), key, on_open_finding or show_finding)
    if focus_finding in cards:
        card = cards[focus_finding]
        ui.timer(.1, lambda: ui.run_javascript(f'document.getElementById("c{card.id}")?.scrollIntoView({{block:"center"}})'), once=True)
    with ui.expansion('Versions et limites du diagnostic'):
        text('Versions : ' + ', '.join(f"{k}={v or 'passé'}" for k, v in data['versions'].items()))
        for limitation in data['limitations']:
            text(limitation, caption=True)
