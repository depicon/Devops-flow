"""Historique de processus distant ; aucune métadonnée libre dans les rapports."""
from nicegui import ui
from devops_flow.ui.components import section, text


def render_remote_history(store, project_id, diagnostic_id=None):
    acquisitions = store.list_remote_acquisitions(project_id, diagnostic_id=diagnostic_id)
    with section('Dernières informations du dépôt distant', 'Informations lues à votre demande, à la date indiquée.'):
        if not acquisitions:
            text('Aucune lecture du dépôt distant enregistrée pour cette sélection.', caption=True)
            return
        options = {a['id']: a['created_at'] + ' · ' + (a['metadata']['last_commit_sha'] or '')[:12] for a in acquisitions}
        selected = {'id': acquisitions[0]['id']}

        @ui.refreshable
        def details():
            acquisition = next(a for a in acquisitions if a['id'] == selected['id'])
            reference, metadata = acquisition['reference'], acquisition['metadata']
            text(reference['provider'] + ' · ' + reference['host'] + '/' + reference['project_path'])
            text('Référence : ' + (reference['ref'] or metadata['default_branch']) + ' · Identifiant du commit : ' + (metadata['last_commit_sha'] or 'indisponible'), caption=True)
            text('Technologies déclarées : ' + (', '.join(acquisition['stack']) or 'aucun indice'), caption=True)
            for warning in acquisition['warnings']:
                text(warning).classes('feedback').props('role=status')
            with ui.expansion('Commits récents', value=True).classes('w-full'):
                if not metadata['recent_commits']:
                    text('Aucun commit disponible.')
                for commit in metadata['recent_commits']:
                    with ui.card().classes('w-full'):
                        text(commit['message'])
                        text(commit['sha'][:12] + ' · ' + commit['author'] + ' · ' + commit['date'], caption=True)
            with ui.expansion('Exécutions d’intégration continue récentes', value=True).classes('w-full'):
                if not metadata['pipelines']:
                    text('Aucune exécution disponible dans les métadonnées acquises. Cela ne prouve pas l’absence de CI.', caption=True)
                for pipeline in metadata['pipelines']:
                    duration = 'durée indisponible' if pipeline['duration'] is None else f"{pipeline['duration']} s"
                    status = {'success':'Réussie', 'failed':'Échouée', 'running':'En cours', 'pending':'En attente', 'canceled':'Annulée', 'cancelled':'Annulée', 'skipped':'Ignorée', 'manual':'Déclenchement manuel', 'created':'Créée'}.get(pipeline['status'], 'Statut à confirmer')
                    text(f"Exécution {pipeline['id']} · {status} · {pipeline['ref']} · {duration}")
        if len(options) > 1:
            def choose(e):
                selected['id'] = e.value
                details.refresh()
            ui.select(options, value=selected['id'], label='Lecture du dépôt consultée', on_change=choose).props('outlined').classes('w-full')
        details()
