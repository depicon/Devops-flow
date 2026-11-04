"""Historique immuable et exports UTF-8 issus d'un objet commun."""
from nicegui import ui
from devops_flow.reports import render_json, render_markdown
from devops_flow.ui.components import button, empty_state, marked, page_header, section, text
from devops_flow.ui.results import render_snapshot
from devops_flow.ui.selection import select_diagnostic
from devops_flow.ui.markdown import safe_markdown
from devops_flow.ui.remote_history import render_remote_history


def render(shell, project):
    page_header('Historique / Rapports', 'Retrouvez un diagnostic sauvegardé et téléchargez son rapport avec le suivi actuel des actions.', project)
    if project is None:
        empty_state(shell, "Créez ou ouvrez un projet dans l'espace Projets.", 'history_projects')
        return
    diagnostic_id = select_diagnostic(shell, project.id)
    if diagnostic_id is None:
        render_remote_history(shell.session.store, project.id)
        return
    diagnostic, _ = shell.session.store.diagnostic_state(project.id, diagnostic_id)
    if shell.session.store.list_remote_acquisitions(project.id, diagnostic_id=diagnostic_id):
        render_remote_history(shell.session.store, project.id, diagnostic_id)
    with ui.expansion('Consulter toutes les lectures du dépôt'):
        render_remote_history(shell.session.store, project.id)
    report = shell.session.report(project.id, diagnostic_id)
    markdown, json_text = render_markdown(report), render_json(report)
    text(f"Diagnostic sauvegardé le {diagnostic.created_at.strftime('%d/%m/%Y à %H:%M')} UTC. Consultation sans nouvelle analyse.", caption=True)
    with ui.expansion('Détails techniques du diagnostic'):
        text(diagnostic.id, caption=True)
        text(diagnostic.snapshot.to_dict()['schema_version'], caption=True)
    with section('Prévisualiser et télécharger', 'Les deux formats partagent la même date et les mêmes données.'):
        text('État du suivi exporté le ' + report.to_dict()['exported_at'], caption=True)
        filename = f'devops-flow-{project.id}-{diagnostic_id}'
        with ui.row():
            button('Télécharger Markdown', lambda: ui.download.content(markdown.encode('utf-8'), filename+'.md', media_type='text/markdown; charset=utf-8'), f'download_md_{diagnostic_id}', primary=True)
            button('Télécharger JSON', lambda: ui.download.content(json_text.encode('utf-8'), filename+'.json', media_type='application/json'), f'download_json_{diagnostic_id}')
        def refresh():
            shell.session.report(project.id, diagnostic_id, refresh=True)
            shell.feedback = ('success', 'Rapport actualisé. Téléchargez le format souhaité.')
            shell.refresh()
        button('Actualiser la date du rapport', refresh, f'report_refresh_{diagnostic_id}')
        with ui.expansion('Confidentialité et contenu du rapport'):
            text(report.to_dict()['export_policy'], caption=True)
        with ui.expansion('Prévisualiser le rapport'):
            with ui.tabs() as tabs:
                md = ui.tab('markdown', label='Aperçu Markdown')
                js = ui.tab('json', label='Aperçu JSON')
            with ui.tab_panels(tabs, value=md).classes('w-full'):
                with ui.tab_panel(md):
                    marked(safe_markdown(markdown), 'report_markdown')
                with ui.tab_panel(js):
                    preview = marked(ui.code(json_text, language='json').classes('w-full'), 'report_json')
                    preview.copy_button.props('aria-label="Copier le JSON" size=md').style('opacity:1')
                    with preview.copy_button:
                        ui.tooltip('Copier le JSON')
    render_snapshot(diagnostic.snapshot.to_dict(), f'history_{project.id}_{diagnostic_id}',
                    on_open_finding=lambda finding_id: shell.navigate('Diagnostic guidé', diagnostic_id, finding_id))
