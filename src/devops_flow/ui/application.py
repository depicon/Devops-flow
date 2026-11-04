"""Enveloppe NiceGUI : cycle de vie, navigation et erreurs neutres."""
import inspect
import os
from pathlib import Path
import sqlite3

from nicegui import run, ui

from devops_flow.db import ProjectStore
from devops_flow.scanner import ScanError
from devops_flow.ui.components import button, marked, text
from devops_flow.ui.state import PAGES, Session
from devops_flow.ui.theme import apply_theme, theme_control

ROOT = Path(__file__).resolve().parents[3]
STORAGE_ERROR = "Le stockage local est inaccessible. Vérifiez les droits d'accès et l'espace disponible, puis réessayez."
SCAN_ERROR = "Analyse refusée : entrée inaccessible, non sûre ou hors limites. Vérifiez la source et les limites décrites dans le README."


class Shell:
    def __init__(self, session):
        self.session = session
        self.client = ui.context.client
        self.client.devops_shell = self
        self.client.on_delete(session.dispose)
        self.busy = False
        self.feedback = None
        apply_theme()
        with ui.header().classes('app-header items-center'):
            menu = marked(ui.button(icon='menu').props('flat aria-label="Ouvrir la navigation"'), 'navigation_toggle')
            text('devops-flow').classes('header-brand')
            with ui.column().classes('project-context'):
                text('Projet ouvert', caption=True)
                self.project_label = marked(text('Aucun projet sélectionné'), 'active_project').classes('project-name')
            with ui.row().classes('header-project-actions items-center gap-2'):
                button('Changer de projet', lambda: self.navigate('Projets'), 'change_project')
                self.settings_button = button('Réglages', self.open_settings, 'project_settings')
            self.dark_mode = theme_control()
        with ui.left_drawer(value=True).props('show-if-above bordered width=260 breakpoint=900') as drawer:
            heading = text('Navigation', caption=True)
            self.nav_buttons = {}
            for page in PAGES:
                self.nav_buttons[page] = marked(ui.button(page, on_click=lambda p=page: self.navigate(p))
                     .props('flat align=left', remove='color').classes('app-nav'), 'nav_' + str(PAGES.index(page)))
            ui.separator()
            self.nav_buttons['Profils d’évaluation'] = marked(ui.button('Profils d’évaluation', on_click=lambda: ui.navigate.to('/profiles'))
                 .props('flat align=left', remove='color').classes('app-nav'), 'nav_profiles')
            text('Données locales · lecture seule du dépôt', caption=True)
        self.drawer = drawer
        menu.on_click(drawer.toggle)
        self.main = ui.column().classes('app-content')
        self.feedback_box = ui.column().classes('fixed bottom-4 right-4 w-auto z-50')
        self.route_path = '/'
        with self.main:
            self.router = ui.sub_pages({
                '/': self._main_route,
                '/projects/new': self._new_project_route,
                '/projects/{project_id}/settings': self._settings_route,
                '/profiles': lambda: self._profile_route('list'),
                '/profiles/new': lambda: self._profile_route('new'),
                '/profiles/import': lambda: self._profile_route('import'),
                '/profiles/{slug}/edit': lambda slug: self._profile_route('edit', slug),
                '/profiles/{slug}': lambda slug: self._profile_route('detail', slug),
                '/projects/{project_id}/docs': self._documentation_route,
            })

    def _main_route(self):
        self.route_path = '/'
        if self.session.page not in PAGES:
            self.session.page = 'Projets'
        self.content = ui.column().classes('w-full gap-4')
        self._render_content()

    def _profile_route(self, page, slug=None):
        from devops_flow.ui.pages import profiles
        self.route_path = '/profiles'
        self.session.navigate('Profils d’évaluation')
        self.content = ui.column().classes('w-full gap-4')
        with self.content:
            try:
                self.update_context()
                profiles.render(self, page, slug)
            except (sqlite3.Error, OSError):
                text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
            except ValueError as error:
                text(str(error), key='data_error').classes('feedback error').props('role=alert')

    def navigate(self, page, diagnostic_id=None, finding_id=None):
        self.session.navigate(page, diagnostic_id, finding_id)
        if page == 'Diagnostic guidé' and diagnostic_id:
            self.session.project().diagnostic_step = 'results'
        self.feedback = None
        if self.route_path != '/':
            ui.navigate.to('/')
        else:
            self.refresh()

    def update_context(self):
        project_id = self.session.active_project_id
        project = self.session.store.get_project(project_id) if project_id else None
        if project is None:
            self.session.active_project_id = None
        self.project_label.set_text(project.name if project else 'Aucun projet sélectionné')
        self.settings_button.set_enabled(project is not None)
        for page, control in self.nav_buttons.items():
            active = self.session.page == page
            control.classes(add='active' if active else '', remove='' if active else 'active')
            control.props('aria-current=page' if active else '', remove='' if active else 'aria-current')
        return project

    def open_settings(self):
        if self.session.active_project_id:
            ui.navigate.to('/projects/' + self.session.active_project_id + '/settings')

    def _new_project_route(self):
        from devops_flow.ui.pages import projects
        self.route_path = '/projects/new'
        self.session.navigate('Projets')
        self.content = ui.column().classes('w-full gap-4')
        with self.content:
            try:
                self.update_context()
                self.show_feedback()
                projects.render_new(self)
            except (sqlite3.Error, OSError):
                text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
            except ValueError as error:
                text(str(error), key='data_error').classes('feedback error').props('role=alert')

    def _settings_route(self, project_id):
        from devops_flow.ui.pages import settings
        self.route_path = '/projects/' + project_id + '/settings'
        self.content = ui.column().classes('w-full gap-4')
        with self.content:
            try:
                project = self.session.store.get_project(project_id)
                if project is None:
                    raise ValueError('Le projet demandé n’est plus disponible. Ouvrez un autre projet.')
                if self.session.active_project_id != project_id:
                    self.session.open_project(project_id)
                self.session.navigate('Réglages du projet')
                self.update_context()
                self.show_feedback()
                settings.render(self, project)
            except (sqlite3.Error, OSError):
                text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
            except ValueError as error:
                text(str(error), key='data_error').classes('feedback error').props('role=alert')

    def show_feedback(self):
        if self.feedback:
            kind, message = self.feedback
            text(message, key='feedback').classes('feedback ' + kind).props('role=status' if kind == 'success' else 'role=alert')

    def _documentation_route(self, project_id):
        from devops_flow.ui.pages import documentation
        self.route_path = '/projects/' + project_id + '/docs'
        if self.session.active_project_id != project_id:
            self.session.open_project(project_id)
        self.session.navigate('Documentation')
        self.content = ui.column().classes('w-full gap-4')
        with self.content:
            if self.feedback:
                kind, message = self.feedback
                text(message, key='feedback').classes('feedback ' + kind).props('role=status' if kind == 'success' else 'role=alert')
            try:
                project = self.session.store.get_project(project_id)
                if project is None:
                    raise ValueError('Le projet demandé n’est plus disponible.')
                self.update_context()
                documentation.render(self, project)
            except (sqlite3.Error, OSError):
                text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
            except ValueError as error:
                text(str(error), key='data_error').classes('feedback error').props('role=alert')

    def refresh(self):
        if not self.client.is_deleted:
            self.router.refresh()

    def _render_content(self):
        if self.client.is_deleted:
            return
        from devops_flow.ui.pages import projects, diagnostic, roadmap, history
        self.content.clear()
        with self.content:
            if self.feedback:
                kind, message = self.feedback
                text(message, key='feedback').classes('feedback ' + kind).props('role=status' if kind == 'success' else 'role=alert')
            try:
                project = self.session.store.get_project(self.session.active_project_id) if self.session.active_project_id else None
                if self.session.active_project_id and project is None:
                    self.session.active_project_id = None
                    text("Ce projet n'est plus disponible. Ouvrez un autre projet.")
                self.update_context()
                renderer = dict(zip(PAGES, (projects.render, diagnostic.render, roadmap.render, history.render)))[self.session.page]
                renderer(self, project)
            except (sqlite3.Error, OSError):
                text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
            except ValueError as error:
                text(str(error), key='data_error').classes('feedback error').props('role=alert')

    def action(self, operation, success=None):
        async def handler(event=None):
            if self.busy:
                return
            self.busy = True
            page, project_id = self.session.page, self.session.active_project_id
            sender = getattr(event, 'sender', None)
            if sender and hasattr(sender, 'disable'):
                sender.disable()
            with self.feedback_box:
                spinner = ui.spinner(size='lg').props('aria-label="Traitement en cours"')
            feedback = None
            try:
                result = operation()
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, tuple) and len(result) == 2 and result[0] in ('success', 'error'):
                    feedback = result
                if success:
                    feedback = ('success', success)
            except ScanError:
                feedback = ('error', SCAN_ERROR)
            except (sqlite3.Error, OSError):
                feedback = ('error', STORAGE_ERROR)
            except ValueError as error:
                feedback = ('error', str(error))
            finally:
                self.busy = False
                if not spinner.is_deleted:
                    spinner.delete()
                if sender and not sender.is_deleted and hasattr(sender, 'enable'):
                    sender.enable()
            if self.client.is_deleted:
                return
            # Les créations choisissent volontairement un nouveau projet ; une navigation externe ne reçoit pas le retour d'un ancien écran.
            if self.session.page == page and (self.session.active_project_id == project_id or page == 'Projets'):
                self.feedback = feedback
                self.refresh()
        return handler


def database_path() -> Path:
    """Réutilise les installations antérieures sans déplacer leurs données."""
    configured = os.environ.get('DEVOPS_FLOW_DB_PATH') or os.environ.get('DEVOPS_ASSIST_DB_PATH')
    if configured:
        return Path(configured)
    current = ROOT / 'var/devops_flow.sqlite3'
    previous = ROOT / 'var/devops_assist.sqlite3'
    if current.exists() or not previous.exists():
        return current
    return previous


async def root():
    store = ProjectStore(database_path())
    try:
        await run.io_bound(store.initialize)
    except (sqlite3.Error, OSError):
        apply_theme()
        text(STORAGE_ERROR, key='storage_error').classes('feedback error').props('role=alert')
        return
    Shell(Session(store))


def main():
    ui.run(root, host='127.0.0.1', port=8501, title='devops-flow', favicon='🧭',
           language='fr', dark=False, reload=False, show=False, on_air=False, show_welcome_message=False)
