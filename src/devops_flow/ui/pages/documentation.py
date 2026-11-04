"""Documentation locale du projet, édition explicite et PDF des révisions sélectionnées."""
from nicegui import run, ui
from devops_flow.documentation import markdown_parts, safe_mermaid
from devops_flow.documentation_pdf import export_documentation
from devops_flow.ui.components import button, marked, page_header, section, text
from devops_flow.ui.markdown import safe_markdown


def documentation_view(markdown, key='doc'):
    for index, (kind, source) in enumerate(markdown_parts(markdown)):
        if kind == 'markdown':
            safe_markdown(source)
        else:
            with ui.column().classes('w-full'):
                try:
                    safe_mermaid(source)
                except ValueError as error:
                    text(str(error)).classes('feedback error').props('role=alert')
                    ui.code(source).classes('w-full')
                    continue

                render_documentation_diagram(source, f'{key}_{index}')


def render_documentation_diagram(content, marker):
    @ui.refreshable
    def diagram():
        with ui.element('div').classes('flow-scroll w-full').props('tabindex=0 aria-label="Diagramme documentaire, défilement horizontal"'):
            chart = marked(ui.mermaid(content, config={'securityLevel': 'strict', 'maxTextSize': 16384,
                           'fontFamily': 'system-ui, sans-serif', 'themeVariables': {'fontSize': '16px'},
                           'flowchart': {'htmlLabels': False, 'useMaxWidth': False}}).classes('flow-mermaid'), marker)
        error_box = ui.column().classes('w-full')
        def failed():
            chart.set_visibility(False)
            error_box.clear()
            with error_box:
                text('Diagramme non rendu. Le code et le reste de la page restent consultables.').classes('feedback error').props('role=alert')
                ui.code(content).classes('w-full')
                button('Réessayer le rendu', diagram.refresh, marker + '_retry')
        chart.on('error', failed)
    diagram()



def render(shell, project):
    store = shell.session.store
    page_header('Documentation', 'Rédigez les procédures du projet, conservez leurs versions et exportez les pages choisies.', project)
    state = shell.session.project(project.id).documentation
    state.setdefault('selected', None)
    state.setdefault('editing', False)
    state.setdefault('drafts', {})
    state.setdefault('export', [])
    pages = store.list_documentation(project.id)
    by_id = {p.id: p for p in pages}
    if state['selected'] not in by_id:
        state['selected'] = pages[0].id if pages and not state['editing'] else None
    selected = by_id.get(state['selected'])
    with ui.row().classes('w-full gap-3'):
        def new():
            state['selected'], state['editing'] = None, True
            shell.refresh()
        button('Créer une page', new, 'docs_new', primary=True)
        button('Retour au projet', lambda: shell.navigate('Projets'), 'docs_back')
    if pages:
        def select(event):
            state['selected'], state['editing'] = event.value, False
            shell.refresh()
        marked(ui.select({p.id: p.title + f' · v{p.version}' for p in pages}, value=state['selected'],
                         label='Page documentaire', on_change=select).props('outlined').classes('w-full'), 'docs_select')
    elif not state['editing']:
        with section('Aucune page documentaire'):
            text('Créez une procédure, un guide de déploiement ou une note de fonctionnement. Ces pages restent locales.')
    if state['editing']:
        draft_key = state['selected'] or 'new'
        draft = state['drafts'].setdefault(draft_key, {'title': selected.title if selected else '',
                      'markdown': selected.markdown if selected else '', 'version': selected.version if selected else 0})
        with section('Éditer la page', 'Sauvegarde explicite ; chaque enregistrement crée une nouvelle version.'):
            marked(ui.input('Titre de la page').props('outlined maxlength=120').bind_value(draft, 'title').classes('w-full'), 'docs_title')
            marked(ui.textarea('Contenu Markdown').props('outlined autogrow').bind_value(draft, 'markdown').classes('w-full'), 'docs_markdown')
            text('Markdown et blocs Mermaid acceptés. HTML actif, images et ressources externes exclus. Maximum 256 Kio par page.', caption=True)

            async def save():
                saved = await run.io_bound(store.save_documentation, project.id, draft['title'], draft['markdown'],
                                           page_id=state['selected'], expected_version=draft['version'])
                state['drafts'].pop(draft_key, None)
                state['selected'], state['editing'] = saved.id, False
            with ui.row():
                button('Enregistrer la version', shell.action(save, 'Nouvelle version documentaire enregistrée.'), 'docs_save', primary=True)
                def reload():
                    state['drafts'].pop(draft_key, None)
                    state['editing'] = False
                    shell.refresh()
                button('Recharger la dernière version', reload, 'docs_reload')
            with ui.expansion('Aperçu de la saisie').classes('w-full'):
                @ui.refreshable
                def preview():
                    try:
                        documentation_view(draft['markdown'], 'docs_preview')
                    except ValueError as error:
                        text(str(error)).classes('feedback error')
                button('Actualiser l’aperçu', preview.refresh, 'docs_preview_refresh')
                preview()
    elif selected:
        with section(selected.title, f'Version {selected.version} · {selected.created_at}'):
            def edit():
                state['editing'] = True
                shell.refresh()
            button('Modifier la page', edit, 'docs_edit')
            try:
                documentation_view(selected.markdown, 'docs_view')
            except ValueError as error:
                text(str(error)).classes('feedback error').props('role=alert')
        with section('Versions de la page'):
            versions = store.documentation_versions(project.id, selected.id)
            version = marked(ui.select({v.version: f'Version {v.version} · {v.created_at}' for v in versions},
                               value=selected.version, label='Version à consulter').props('outlined').classes('w-full'), 'docs_version')
            @ui.refreshable
            def old_preview():
                old = next(v for v in versions if v.version == version.value)
                text(old.title)
                try:
                    documentation_view(old.markdown, 'docs_old')
                except ValueError as error:
                    text(str(error)).classes('feedback error')
            version.on_value_change(lambda _: old_preview.refresh())
            with ui.expansion('Consulter la version sélectionnée').classes('w-full'):
                old_preview()
            async def restore():
                await run.io_bound(store.restore_documentation, project.id, selected.id, version.value, expected_version=selected.version)
                state['drafts'].pop(selected.id, None)
            button('Restaurer comme nouvelle version', shell.action(restore, 'Version restaurée dans une nouvelle révision.'), 'docs_restore')
    if pages:
        with section('Exporter la documentation', 'Le PDF contient les versions affichées des pages sélectionnées, dans l’ordre indiqué ci-dessous.'):
            state['export'] = [i for i in state['export'] if i in by_id]
            marked(ui.select({p.id: p.title + f' · v{p.version}' for p in pages}, multiple=True, label='Pages à exporter')
                   .props('outlined use-chips').bind_value(state, 'export').classes('w-full'), 'docs_export_selection')
            def show_order():
                state['export'] = list(dict.fromkeys(state['export']))
                order.refresh()
            button('Afficher / actualiser l’ordre', show_order, 'docs_export_order')
            @ui.refreshable
            def order():
                for index, identifier in enumerate(state['export']):
                    with ui.row().classes('items-center'):
                        text(f'{index + 1}. {by_id[identifier].title}')
                        def move(i=index, delta=-1):
                            items = state['export']
                            if 0 <= i + delta < len(items):
                                items[i], items[i + delta] = items[i + delta], items[i]
                                order.refresh()
                        button('Monter', move, f'docs_up_{identifier}').props('aria-label="Monter la page dans le PDF"')
                        button('Descendre', lambda i=index, action=move: action(i, 1), f'docs_down_{identifier}').props('aria-label="Descendre la page dans le PDF"')
            order()
            async def export():
                # Objets immuables lus ensemble au rendu : une édition parallèle ne change pas cet export.
                selection = tuple(by_id[i] for i in state['export'] if i in by_id)
                result = await run.io_bound(export_documentation, selection)
                if shell.client.is_deleted or shell.session.active_project_id != project.id or shell.session.page != 'Documentation':
                    return
                ui.download.content(result.content, 'documentation-' + project.id + '.pdf', media_type='application/pdf')
                return ('success', 'PDF généré avec ' + result.engine + '. ' + ' '.join(result.warnings))
            button('Télécharger le PDF', shell.action(export), 'docs_export_pdf', primary=True)
