"""Scan temporaire, source et réception ZIP en mémoire."""
from dataclasses import asdict
from nicegui import ui
from devops_flow.ui.components import FINDING_LABELS, button, controls_explainer, finding_card, marked, section, text
from devops_flow.ui.upload import MemoryUpload
from devops_flow.rules import select_findings
from devops_flow.ui.architecture import render_architecture, render_declarations

STATUS_LABELS = FINDING_LABELS


def render_scan(shell, project_id):
    state = shell.session.project(project_id)
    with ui.element('div').props('id=scan-technique-cible').classes('w-full'), section('Analyse du dépôt', 'Les configurations sont lues sans exécuter votre code ni modifier le dépôt.'):
        text('Analyse en lecture seule des manifests et configurations Docker, Compose et GitLab CI par composant. Aucun code exécuté.', caption=True)
        text('Les contrôles d’intégration continue portent uniquement sur GitLab CI, y compris pour un dépôt hébergé sur GitHub. Les exécutions GitHub Actions sont des informations séparées.', caption=True)
        profile = shell.session.store.project_profile(project_id)
        controls_explainer(profile, key='scan_controls_count')

        def mode_changed(event):
            shell.session.change_source(project_id, mode=event.value)
            shell.feedback = None
            shell.refresh()
        marked(ui.radio(['Archive ZIP', 'Dossier local', 'Dépôt distant'], value=state.mode, on_change=mode_changed).props('inline'), f'scan_mode_{project_id}')
        if state.mode == 'Archive ZIP':
            def begin():
                state.invalidate()
                result_box.clear()
                return state.revision

            def rejected(ticket=None):
                if ticket is not None and state.revision != ticket:
                    return
                state.invalidate()
                if shell.session.active_project_id == project_id and shell.session.page == 'Diagnostic guidé':
                    shell.feedback = ('error', 'Archive refusée : choisissez un seul ZIP de 10 Mio maximum.')
                    shell.refresh()

            def removed():
                state.invalidate()
                if shell.session.active_project_id == project_id and shell.session.page == 'Diagnostic guidé':
                    shell.feedback = None
                    shell.refresh()

            async def received(file, revision):
                if shell.session.active_project_id != project_id or shell.session.page != 'Diagnostic guidé':
                    return
                data = await file.read()
                if shell.session.accept_zip(project_id, data, revision):
                    if shell.session.active_project_id == project_id and shell.session.page == 'Diagnostic guidé':
                        shell.feedback = ('success', 'ZIP reçu en mémoire. Cliquez sur Analyser le dépôt.')
                        shell.refresh()
            upload = MemoryUpload(label='Choisir un ZIP', auto_upload=True, on_start=begin,
                                  on_file=received, on_rejected=lambda: rejected(), on_error=rejected)
            marked(upload.props('accept=.zip').classes('w-full'), f'scan_zip_{project_id}')
            upload.on('removed', removed)
            if state.pending_zip is not None:
                text('Archive prête à être analysée.', caption=True)
                button('Retirer le ZIP', removed, f'scan_clear_{project_id}')
        elif state.mode == 'Dossier local':
            def path_changed(event):
                shell.session.change_source(project_id, path=event.value)
                shell.feedback = None
                # Ne pas reconstruire le champ pendant sa saisie.
                result_box.clear()
            marked(ui.input('Chemin du dossier local', value=state.path, on_change=path_changed).props('outlined').classes('w-full'), f'scan_path_{project_id}')
            text("Dossier sur la machine qui lance l’application ; parcours borné des sous-dossiers, hors secrets et dépendances.", caption=True)
        else:
            shell.session.load_remote(project_id)
            text('Analyse distante limitée à la racine. Pour une architecture multi-composants, utilisez un ZIP ou un dossier local.', caption=True).classes('feedback')

            def changed(name, value):
                setattr(state, name, value or '')
                state.invalidate()
                result_box.clear()

            marked(ui.select({'gitlab': 'GitLab', 'github': 'GitHub'}, value=state.remote_provider,
                             label='Fournisseur', on_change=lambda e: changed('remote_provider', e.value)).props('outlined'), f'remote_provider_{project_id}')
            marked(ui.input('URL HTTPS du dépôt', value=state.remote_url,
                            on_change=lambda e: changed('remote_url', e.value)).props('outlined').classes('w-full'), f'remote_url_{project_id}')
            marked(ui.input('Branche, étiquette ou identifiant de commit (facultatif)', value=state.remote_ref,
                            on_change=lambda e: changed('remote_ref', e.value)).props('outlined').classes('w-full'), f'remote_ref_{project_id}')
            token = marked(ui.input('Jeton personnel (facultatif pour un dépôt public)', value=state.remote_token,
                                    password=True, password_toggle_button=True,
                                    on_change=lambda e: changed('remote_token', e.value)).props('outlined autocomplete=off').classes('w-full'), f'remote_token_{project_id}')
            text('Le jeton donne accès à un dépôt privé. Il reste en mémoire et doit être ressaisi après rechargement.', caption=True)
            with ui.expansion('Permissions et options d’accès'):
                text('GitLab : read_api recommandé. GitHub : Contents en lecture et Actions en lecture pour les exécutions. Seuls les serveurs HTTPS publics sont accessibles.', caption=True)
            button('Effacer le jeton', lambda: token.set_value(''), f'remote_clear_token_{project_id}')
            text('La lecture se fait à votre demande. Les fichiers de projet indiquent les technologies déclarées ; ils n’ajoutent pas de contrôle.', caption=True)
        async def analyze():
            if not await shell.session.analyze(project_id):
                raise ValueError('La source a changé pendant l’analyse. Relancez le scan pour la source actuelle.')
        button('Analyser le dépôt', shell.action(analyze,
               'Analyse terminée. Continuez vers les résultats pour créer le diagnostic avec cette source.'), f'scan_run_{project_id}', primary=True)
        if state.mode != 'Dépôt distant':
            render_declarations(shell, project_id)
        result_box = ui.column().classes('w-full gap-4')
        with result_box:
            result = state.scan
            if result is None:
                text('Aucun scan disponible. Choisissez une source et lancez l’analyse, ou finalisez avec le questionnaire enregistré seul.', caption=True)
                return
            text('Source du dernier scan : ' + ('Dépôt distant' if state.acquisition else 'Dossier local' if result.source == 'local' else 'Archive ZIP') + '.', caption=True)
            if state.acquisition:
                acquisition = state.acquisition
                text(f'{acquisition.reference.provider} · SHA {acquisition.metadata.last_commit_sha} · acquisition {acquisition.method}', caption=True)
                text('Technologies déclarées : ' + (', '.join(acquisition.stack) or 'aucun indice dans les manifests ciblés'), caption=True)
                for warning in acquisition.warnings:
                    text(warning).classes('feedback').props('role=status')
            if result.source == 'zip' and state.pending_zip is None and not state.acquisition:
                text('Le résultat du dernier ZIP reste disponible pour finaliser. Sélectionnez à nouveau une archive pour relancer l’analyse.', caption=True)
            with ui.expansion('Périmètre et versions du scan'):
                text(f'{result.scanner_version} · {result.rules_version}. Relancez l’analyse si les fichiers ont changé.', caption=True)
                text('Fichiers ciblés indexés : ' + (', '.join(result.files) or 'aucun dans le périmètre supporté'))
            profile = shell.session.store.project_profile(project_id)
            count = len(profile.rules)
            text(f'{count} contrôle' + (' activé' if count == 1 else 's activés') + ' sur 6 disponibles. Les contrôles désactivés ne produisent pas de résultat.', caption=True)
            architecture = getattr(result, 'architecture', None)
            with ui.tabs().classes('w-full').props('align=left outside-arrows mobile-arrows') as tabs:
                architecture_tab = marked(ui.tab('architecture', label='Architecture'), f'scan_architecture_tab_{project_id}')
                checks_tab = ui.tab('checks', label='Contrôles techniques')
            with ui.tab_panels(tabs, value=architecture_tab if architecture and architecture.components else checks_tab).classes('w-full'):
                with ui.tab_panel(architecture_tab).classes('p-0'):
                    render_architecture(architecture.to_dict() if architecture else None, f'current_{project_id}', current=True)
                with ui.tab_panel(checks_tab).classes('p-0 flex flex-col gap-3'):
                    names = {c.id: '(racine)' if c.path == '.' else c.path for c in architecture.components} if architecture else {}
                    if names:
                        text('Les contrôles activés sont évalués pour chaque composant ; les configurations non assignées restent au niveau projet.', caption=True)
                    for finding in select_findings(result.findings, profile=profile):
                        if names:
                            text('Périmètre : ' + names.get(getattr(finding, 'scope_id', 'project'), 'Projet / configurations communes'), caption=True)
                        finding_card(asdict(finding))
