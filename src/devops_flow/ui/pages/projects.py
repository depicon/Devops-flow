"""Liste des projets ; création séparée pour privilégier la reprise du travail."""
from nicegui import run, ui
from devops_flow.demo import load_demo, repository_zip
from devops_flow.models import MAX_PROJECT_DESCRIPTION_LENGTH, MAX_PROJECT_NAME_LENGTH
from devops_flow.ui.components import button, marked, navigation_button, page_header, profile_badge, profile_selector, section, text


def render(shell, project):
    store = shell.session.store
    page_header('Projets', 'Ouvrez un projet pour évaluer vos pratiques et suivre les améliorations.')
    button('Nouveau projet', lambda: ui.navigate.to('/projects/new'), 'project_new', primary=True)
    if project:
        with section('Reprendre le projet ouvert'):
            text(project.name).classes('section-heading')
            if project.description:
                text(project.description, caption=True)
            profile_badge(store.project_profile(project.id))
            with ui.row().classes('gap-3'):
                navigation_button(shell, 'Préparer le diagnostic', 'Diagnostic guidé', 'projects_diagnostic', primary=True)
                button('Documentation du projet', lambda: ui.navigate.to('/projects/' + project.id + '/docs'), 'projects_docs')
                button('Réglages du projet', shell.open_settings, 'projects_settings')
    with section('Projets enregistrés'):
        projects = store.list_projects()
        if not projects:
            text('Aucun projet enregistré. Créez votre premier projet ou explorez la démonstration.')
        for item in projects:
            with ui.row().classes('w-full items-center justify-between gap-3'):
                with ui.column().classes('gap-1 min-w-0'):
                    text(item.name).classes('font-semibold')
                    text(f"Créé le {item.created_at.strftime('%d/%m/%Y à %H:%M')} UTC", caption=True)
                    if item.id == shell.session.active_project_id:
                        text('Ouvert actuellement', caption=True).classes('status-pill')
                def choose(project_id=item.id):
                    shell.session.open_project(project_id)
                    shell.feedback = None
                    shell.refresh()
                button('Ouvrir', choose, f'open_{item.id}')
    with ui.expansion('Découvrir avec une démo'):
        text('Un projet fictif pour explorer les résultats et le suivi. Chaque chargement crée un projet distinct.', caption=True)
        async def demo():
            result, _ = await run.io_bound(load_demo, store)
            shell.session.open_project(result.id)
        button('Charger une démo', shell.action(demo, 'Démo fictive enregistrée. Ouvrez le diagnostic pour explorer les résultats.'), 'load_demo')
        button('Télécharger le ZIP fictif', lambda: ui.download.content(repository_zip(), 'devops-flow-demo-fictive.zip', media_type='application/zip'), 'download_demo_zip')


def render_new(shell):
    store = shell.session.store
    page_header('Nouveau projet', 'Un nom suffit pour commencer. Vous pourrez adapter les réglages ensuite.')
    button('Retour aux projets', lambda: shell.navigate('Projets'), 'project_new_back')
    with section('Informations du projet'):
        name = marked(ui.input('Nom du projet').props(f'outlined maxlength={MAX_PROJECT_NAME_LENGTH}').classes('w-full'), 'project_name')
        description = marked(ui.textarea('Description (facultative)').props(f'outlined maxlength={MAX_PROJECT_DESCRIPTION_LENGTH}').classes('w-full'), 'project_description')
        available_profiles = store.list_profiles()
        text('Le profil définit les questions et les contrôles utilisés pour le diagnostic.', caption=True)
        selected = marked(profile_selector(available_profiles, 'baseline', lambda event: None), 'project_profile')
        text('Le référentiel de base est prêt à l’emploi. Le choix reste modifiable dans les réglages du projet.', caption=True)
        async def create():
            profile = next(p for p in available_profiles if p.slug == selected.value)
            result = await run.io_bound(store.create_project, name.value or '', description.value or '',
                                       profile_slug=profile.slug, profile_version=profile.version)
            shell.session.open_project(result.id)
            ui.navigate.to('/')
        button('Créer le projet', shell.action(create, 'Projet créé. Vous pouvez préparer son diagnostic.'), 'project_create', primary=True)
