"""Projection volontaire depuis Plan d'action ; identifiants côté serveur seulement."""
from datetime import datetime, timezone
import json
from queue import Empty, Queue

from nicegui import run, ui

from devops_flow.integrations.config import load_credentials
from devops_flow.integrations.projection import CHECKLIST_NAME, STATUSES
from devops_flow.integrations.trello import HttpTrelloClient, TrelloError
from devops_flow.integrations.trello_projection import TrelloProjector
from devops_flow.ui.components import button, marked, section, text


def render_trello(shell, project, diagnostic_id, actions):
    store = shell.session.store
    state = shell.session.project(project.id).trello
    if state.get('selected_diagnostic') != diagnostic_id:
        state.pop('preview', None)
        state['selected_diagnostic'] = diagnostic_id
    with section('Trello', 'Projetez les actions de ce diagnostic. Les changements effectués dans Trello restent dans Trello.'):
        try:
            credentials = load_credentials()
        except ValueError as error:
            credentials = None
            text(str(error)).classes('feedback error').props('role=alert')
        text('Configuration Trello disponible.' if credentials else
             'Trello est facultatif. Configurez TRELLO_API_KEY et TRELLO_TOKEN dans l’environnement ou le .env de devops-flow.', caption=True)
        if state.get('username'):
            text('Connexion vérifiée : ' + state['username'], key='trello_username')

        async def ping():
            def work():
                client = HttpTrelloClient(credentials)
                try:
                    return client.ping()
                finally:
                    client.close()
            state['username'] = await run.io_bound(work)
            return 'success', 'Connexion Trello vérifiée : ' + state['username']
        test = button('Tester la connexion', shell.action(ping), 'trello_ping')
        if credentials is None:
            test.disable()

        active = store.trello_projection(project.id)
        pending = store.trello_projection(project.id, pending=True)
        linked = pending if pending and pending['board_json'] else active
        if linked and linked['board_json']:
            board = json.loads(linked['board_json'])
            marked(ui.link('Ouvrir le board Trello', board['url'], new_tab=True), 'trello_board_link')
        options = ({'resume': 'Reprendre la projection', 'recreate': 'Recréer un nouveau board'} if pending else
                   {'append': 'Ajouter les nouvelles actions uniquement', 'recreate': 'Recréer un nouveau board'} if active else
                   {'create': 'Créer un board dédié'})
        mode = state.get('mode')
        if mode not in options:
            mode = next(iter(options))
        state['mode'] = mode
        def change_mode(event):
            state['mode'] = event.value
            state.pop('preview', None)
            shell.refresh()
        marked(ui.select(options, value=mode, label='Projection Trello', on_change=change_mode)
               .props('outlined').classes('w-full'), 'trello_mode')
        if mode == 'recreate':
            text('Un nouveau board sera créé pour le diagnostic sélectionné. Les boards précédents seront conservés.', caption=True)
        if pending and mode != 'recreate':
            text('Une projection incomplète sera reprise avec les données de son aperçu enregistré.', caption=True)

        async def preview():
            state['preview'] = await run.io_bound(TrelloProjector(store).preview, project.id, diagnostic_id,
                                                  'create' if state['mode'] == 'resume' else state['mode'])
        button('Aperçu Trello', shell.action(preview), 'trello_preview')
        if not actions and not pending:
            text('Aucune action à projeter pour ce diagnostic.', caption=True)
        plan = state.get('preview')
        if plan is None:
            return
        text('Aperçu sans appel à Trello').classes('section-heading')
        text(plan.board_name)
        text(f'{len(plan.cards)} carte(s) · 4 listes · une checklist par carte', caption=True)
        if plan.diagnostic_id != diagnostic_id:
            text('Reprise du diagnostic : ' + plan.diagnostic_id, caption=True)
        text('Données envoyées : nom du projet pour le board, recommandations, priorités, références et critères. Les notes et commentaires locaux restent privés.', caption=True)
        for status, label in STATUSES.items():
            group = [c for c in plan.cards if c.status == status]
            with ui.expansion(f'{label} · {len(group)} carte(s)').classes('w-full'):
                for card in group:
                    with ui.expansion(card.name).classes('w-full'):
                        text(card.description)
                        text(CHECKLIST_NAME, caption=True)
                        for criterion in card.criteria:
                            text('☐ ' + criterion)
        if not plan.cards:
            text('Aucune nouvelle action à ajouter. Les cartes déjà projetées sont conservées.', caption=True)
        progress = text('', key='trello_progress').props('role=status aria-live=polite')
        queue = Queue()

        async def create():
            create_button.disable()
            def drain():
                while True:
                    try:
                        value = queue.get_nowait()
                    except Empty:
                        break
                    # Ne pas exposer les clés d'étapes internes dans le parcours.
                    progress.set_text(value if value.startswith('Carte ') else 'Création des éléments du board…')
            timer = ui.timer(.1, drain)
            def work():
                client = HttpTrelloClient(credentials)
                try:
                    return TrelloProjector(store, client).project(plan, queue.put)
                finally:
                    client.close()
            try:
                result = await run.io_bound(work)
            except TrelloError as error:
                if error.retry_at:
                    date = datetime.fromtimestamp(error.retry_at, timezone.utc).strftime('%d/%m/%Y à %H:%M:%S UTC')
                    return 'error', f'{error} Reprise possible à partir du {date}.'
                raise
            finally:
                timer.cancel()
                if not create_button.is_deleted:
                    create_button.enable()
            if result is None:  # Arrêt du client/serveur pendant run.io_bound.
                return
            state.pop('preview', None)
            state['mode'] = 'append'
            return 'success', f'Projection Trello terminée : {len(result.cards)} carte(s). Vous pouvez ouvrir le board.'
        create_button = button('Créer le board Trello' if plan.mode != 'append' else 'Ajouter les nouvelles actions uniquement',
                               shell.action(create), 'trello_create', primary=True)
        if not plan.cards or credentials is None:
            create_button.disable()
