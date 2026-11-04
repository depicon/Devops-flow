"""Suivi des actions propres à un diagnostic."""
from nicegui import run, ui
from devops_flow.roadmap import MAX_COMMENT_LENGTH, STATUS_LABELS
from devops_flow.ui.components import button, empty_state, kpi_card, marked, page_header, section, text
from devops_flow.ui.selection import select_diagnostic
from devops_flow.ui.copy import finding_title
from devops_flow.ui.trello import render_trello
from devops_flow.ui.architecture import scope_labels


def render(shell, project):
    page_header("Plan d'action", 'Suivez les actions du diagnostic sélectionné et organisez leur ordre de traitement.', project)
    if project is None:
        empty_state(shell, "Créez ou ouvrez un projet dans l'espace Projets.", 'roadmap_projects')
        return
    diagnostic_id = select_diagnostic(shell, project.id)
    if diagnostic_id is None:
        return
    diagnostic, actions = shell.session.store.diagnostic_state(project.id, diagnostic_id)
    data = diagnostic.snapshot.to_dict()
    recommendations = {r['id']: r for r in data['recommendations']}
    names = scope_labels(data)
    text(f"Actions du diagnostic du {diagnostic.created_at.strftime('%d/%m/%Y à %H:%M')} UTC", caption=True)
    with ui.expansion('Référence du diagnostic'):
        text(diagnostic.id, caption=True)
    render_trello(shell, project, diagnostic_id, actions)
    with ui.row().classes('w-full gap-4'):
        for status, label in STATUS_LABELS.items():
            kpi_card(label, sum(a.status == status for a in actions))
    text('Choisissez une action, mettez à jour son statut, puis enregistrez le suivi. Vous pouvez réordonner les actions.', caption=True)
    if not actions:
        text('Aucune action proposée pour ce diagnostic ; cela ne certifie pas les pratiques.')
    for index, action in enumerate(actions):
        rec = recommendations.get(action.recommendation_id)
        if rec is None:
            raise ValueError('Une action enregistrée est incompatible avec ce diagnostic.')
        with section(f"{index + 1}. {rec['title']}"):
            if len(names) > 1:
                text('Périmètre : ' + names[rec['scope_id']], caption=True)
            text(rec['description'])
            text(f"Priorité initiale {rec['priority']:g} = impact {rec['impact']} / effort {rec['effort']}", caption=True)
            with ui.expansion('Origine et dates de l’action'):
                text('Constats : ' + ', '.join(finding_title(fid, data['findings']) for fid in rec['source_finding_ids']))
                text(f"{rec['id']} · Créée : {action.created_at.isoformat()} · Mise à jour : {action.updated_at.isoformat()}", caption=True)
            status = marked(ui.select(STATUS_LABELS, value=action.status, label='Statut').props('outlined').classes('w-full'), f'action_status_{action.id}')
            notes = marked(ui.textarea('Commentaire local (non exporté)', value=action.notes).props(f'outlined maxlength={MAX_COMMENT_LENGTH}').classes('w-full'), f'action_notes_{action.id}')
            async def save(action_id=action.id, status_field=status, notes_field=notes):
                await run.io_bound(shell.session.store.update_action, project.id, diagnostic_id, action_id,
                                   status=status_field.value, notes=notes_field.value or '')
            button('Enregistrer le suivi', shell.action(save, 'Suivi enregistré. Vous pouvez poursuivre les autres actions ou consulter le rapport actualisé.'), f'action_save_{action.id}', primary=True)
            text('Enregistrez le formulaire avant de déplacer l’action.', caption=True)
            with ui.row():
                for label, offset, prefix, disabled in [('Monter', -1, 'up', index == 0), ('Descendre', 1, 'down', index == len(actions)-1)]:
                    async def move(action_id=action.id, position=index+offset):
                        await run.io_bound(shell.session.store.move_action, project.id, diagnostic_id, action_id, position)
                    control = button(label, shell.action(move, 'Ordre enregistré. Les priorités initiales sont conservées.'), f'action_{prefix}_{action.id}')
                    if disabled:
                        control.disable()
