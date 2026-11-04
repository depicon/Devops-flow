"""Réglages explicites d'un projet ; publication et assignation restent distinctes."""
from nicegui import run, ui
from devops_flow.ui.components import button, marked, navigation_button, page_header, profile_badge, profile_selector, section, text


def render(shell, project):
    store = shell.session.store
    current = store.project_profile(project.id)
    page_header('Réglages du projet', 'Modifiez le projet et choisissez les questions et contrôles de ses prochains diagnostics.')
    navigation_button(shell, 'Retour au diagnostic', 'Diagnostic guidé', 'settings_diagnostic')
    with section('Informations du projet'):
        name = marked(ui.input('Nom du projet', value=project.name).props('outlined maxlength=120').classes('w-full'), 'project_edit_name')
        description = marked(ui.textarea('Description (facultative)', value=project.description).props('outlined maxlength=2000').classes('w-full'), 'project_edit_description')
    with section('Profil utilisé pour l’analyse', 'Le profil définit les questions, les contrôles et les seuils d’interprétation.'):
        profile_badge(current)
        available = store.list_profiles()
        def selected(event):
            options = {p.version: f'Version {p.version}' + (' · dernière publiée' if p.is_current else '') for p in store.list_profile_versions(event.value)}
            version.set_options(options, value=max(options))
        profile = marked(profile_selector(available, project.profile_slug, selected), 'project_edit_profile')
        versions = store.list_profile_versions(project.profile_slug)
        version = marked(ui.select({p.version: f'Version {p.version}' + (' · dernière publiée' if p.is_current else '') for p in versions},
                                   value=project.profile_version, label='Version utilisée').props('outlined').classes('w-full'), 'project_edit_version')
        text('Publier une version ne l’applique pas aux projets. Chaque version conserve son questionnaire enregistré ; les anciens diagnostics restent inchangés.', caption=True)
        def edit_profile():
            path = '/profiles/new' if profile.value == 'baseline' else '/profiles/' + profile.value + '/edit'
            ui.navigate.to(path)
        button('Modifier les questions et contrôles', edit_profile, 'settings_edit_profile')
        text('Le référentiel de base est protégé : ce bouton permet de créer un profil personnalisé.', caption=True)
    async def update():
        await run.io_bound(store.update_project, project.id, name.value or '', description.value or '',
                           profile_slug=profile.value, profile_version=version.value)
        state = shell.session.project(project.id)
        if (profile.value, version.value) != (project.profile_slug, project.profile_version):
            state.diagnostic_step = 'prepare'
            state.diagnostic_sources = 'both'
            # load_form recharge le brouillon propre à la nouvelle version.
            state.profile_ref = None
            state.include_calms = state.include_scan = None
            state.new_submission()
    def save(event):
        changed = (profile.value, version.value) != (project.profile_slug, project.profile_version)
        state = shell.session.project(project.id)
        saved = store.load_calms(project.id, profile=current)
        if changed and state.questionnaire_dirty(saved):
            with ui.dialog() as dialog, ui.card().classes('app-card'):
                text('Changer de profil sans enregistrer le questionnaire ?').classes('section-heading')
                text('Les modifications non enregistrées du questionnaire seront perdues. Le questionnaire déjà enregistré pour chaque version est conservé.')
                def cancel_change():
                    dialog.close()
                    state.diagnostic_step = 'questionnaire'
                    shell.navigate('Diagnostic guidé')
                button('Revenir au questionnaire', cancel_change, 'settings_cancel_change')
                button('Changer de profil', shell.action(update, 'Projet et profil enregistrés.'), 'settings_confirm_change', primary=True).on_click(dialog.close)
            dialog.open()
        else:
            return shell.action(update, 'Projet et profil enregistrés.')(event)
    button('Enregistrer les réglages', save, 'project_update', primary=True)
