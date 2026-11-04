"""Sélection isolée par projet et espace."""
from nicegui import ui
from devops_flow.ui.components import marked, navigation_button, text


def select_diagnostic(shell, project_id):
    items = shell.session.store.list_diagnostics(project_id)
    if not items:
        text('Aucun diagnostic enregistré. Finalisez un diagnostic dans Diagnostic guidé.')
        navigation_button(shell, 'Préparer un diagnostic', 'Diagnostic guidé', f'first_{project_id}', primary=True)
        return None
    state = shell.session.project(project_id)
    space = shell.session.page
    by_id = {d.id: f"Diagnostic du {d.created_at.strftime('%d/%m/%Y à %H:%M:%S')} UTC · n° {len(items) - index}" for index, d in enumerate(items)}
    if state.selected.get(space) not in by_id:
        state.selected[space] = items[0].id

    def choose(event):
        state.selected[space] = event.value
        shell.feedback = None
        shell.refresh()
    prefix = 'history' if space == 'Historique / Rapports' else 'roadmap'
    marked(ui.select(by_id, value=state.selected[space], label='Diagnostic enregistré', on_change=choose)
           .props('outlined').classes('w-full'), f'{prefix}_diagnostic_{project_id}')
    return state.selected[space]
