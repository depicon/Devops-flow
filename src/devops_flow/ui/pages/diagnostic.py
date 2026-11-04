"""Questionnaire explicitement enregistré, scan et finalisation."""
from dataclasses import asdict
from nicegui import ui
from devops_flow.calms import MAX_NOTE_LENGTH, calculate_scores
from devops_flow.ui.components import button, controls_explainer, empty_state, marked, page_header, profile_badge, score_badge, section, text
from devops_flow.ui.scan import render_scan
from devops_flow.ui.results import render_results

from devops_flow.ui.copy import ANSWER_LABELS, dimension_label


def render(shell, project):
    page_header('Diagnostic guidé', 'Préparez les sources, créez le diagnostic, puis choisissez les améliorations à suivre.')
    if project is None:
        empty_state(shell, "Créez ou ouvrez un projet dans l'espace Projets.", 'diagnostic_projects')
        return
    state = shell.session.project(project.id)
    profile = shell.session.store.project_profile(project.id)
    try:
        saved = shell.session.store.load_calms(project.id, profile=profile)
    except ValueError as error:
        saved = None
        text(str(error)).classes('feedback error').props('role=alert')
    if state.focused_finding:
        state.diagnostic_step = 'results'
    steps = [('prepare', '1. Préparer'), ('questionnaire', '2. Questionnaire'),
             ('repository', '3. Dépôt'), ('results', '4. Résultats')]
    def choose_step(event):
        state.diagnostic_step = event.value
        if event.value != 'results':
            state.focused_finding = None
    with marked(ui.tabs(value=state.diagnostic_step, on_change=choose_step)
                .classes('w-full diagnostic-steps').props('align=left outside-arrows mobile-arrows'), 'diagnostic_steps') as tabs:
        for name, label in steps:
            marked(ui.tab(name, label=label), 'step_' + name)
    def go(step):
        state.diagnostic_step = step
        tabs.value = step
    with ui.tab_panels(tabs, value=state.diagnostic_step, animated=False).classes('w-full'):
        with ui.tab_panel('prepare').classes('p-0 flex flex-col gap-4'):
            with section('Que souhaitez-vous évaluer ?'):
                text('Vous pouvez décrire les pratiques de votre équipe, examiner les configurations du dépôt, ou combiner les deux.', caption=True)
                def sources(event):
                    state.diagnostic_sources = event.value
                    state.include_calms = event.value in ('both', 'calms')
                    state.include_scan = event.value in ('both', 'scan')
                    shell.refresh()
                marked(ui.radio({'both':'Pratiques et dépôt', 'calms':'Pratiques de l’équipe uniquement', 'scan':'Dépôt uniquement'},
                       value=state.diagnostic_sources, on_change=sources), 'diagnostic_sources')
                profile_badge(profile)
                text('Le profil définit les questions et les contrôles. Sa version est celle choisie dans les réglages du projet.', caption=True)
                button('Choisir ou modifier le profil', shell.open_settings, 'diagnostic_settings')
            with ui.card().classes('app-card next-action'):
                text('Votre prochaine action').classes('section-heading')
                if state.submitted:
                    text('Votre diagnostic est sauvegardé. Consultez ses résultats et les actions proposées.')
                    target, label = 'results', 'Voir les résultats'
                elif state.include_calms and saved is None:
                    text('Enregistrez le questionnaire sur les pratiques de votre équipe.')
                    target, label = 'questionnaire', 'Commencer le questionnaire'
                elif state.include_scan and state.scan is None:
                    text('Choisissez un dépôt et lancez son analyse.')
                    target, label = 'repository', 'Analyser un dépôt'
                elif saved is not None or state.scan is not None:
                    text('Des sources sont disponibles. Vérifiez la sélection avant de créer votre diagnostic.')
                    target, label = 'results', 'Vérifier et créer le diagnostic'
                elif state.diagnostic_sources == 'scan':
                    text('Choisissez un dépôt et lancez son analyse.')
                    target, label = 'repository', 'Analyser un dépôt'
                else:
                    text('Commencez par décrire les pratiques de votre équipe.')
                    target, label = 'questionnaire', 'Commencer le questionnaire'
                button(label, lambda: go(target), 'diagnostic_next', primary=True)
                text('Questionnaire : ' + ('enregistré' if saved else 'à enregistrer') + ' · Dépôt : ' +
                     ('analysé dans cette session' if state.scan else 'à analyser'), caption=True)
            controls_explainer(profile)
        with ui.tab_panel('questionnaire').classes('p-0 flex flex-col gap-4'):
            if state.diagnostic_sources == 'scan':
                text('Étape non retenue pour le parcours « Dépôt uniquement ». Vous pouvez modifier ce choix dans Préparer.', caption=True)
            try:
                render_calms(shell, project)
            except ValueError as error:
                text(str(error)).classes('feedback error').props('role=alert')
            button('Continuer vers ' + ('les résultats' if state.diagnostic_sources == 'calms' else 'le dépôt'),
                   lambda: go('results' if state.diagnostic_sources == 'calms' else 'repository'), 'questionnaire_continue')
        with ui.tab_panel('repository').classes('p-0 flex flex-col gap-4'):
            if state.diagnostic_sources == 'calms':
                text('Étape non retenue pour le parcours « Pratiques uniquement ». Vous pouvez modifier ce choix dans Préparer.', caption=True)
            render_scan(shell, project.id)
            button('Vérifier les sources et créer le diagnostic', lambda: go('results'), 'repository_continue')
        with ui.tab_panel('results').classes('p-0 flex flex-col gap-4'):
            render_results(shell, project.id)


def render_calms(shell, project):
    state = shell.session.load_form(project.id)
    profile = shell.session.store.project_profile(project.id)
    with section('Questionnaire sur les pratiques', 'Les réponses sont sauvegardées lorsque vous cliquez sur Enregistrer.'):
        text('CALMS regroupe cinq aspects : culture, automatisation, amélioration du travail, mesure et partage des connaissances. Répondez selon vos pratiques actuelles ; « Je ne sais pas » reste une réponse valide.', caption=True)
        saved_at_render = shell.session.store.load_calms(project.id, profile=profile)
        dirty = marked(text('', caption=True), 'questionnaire_dirty').props('role=status')
        def update_dirty():
            dirty.set_text('Modifications non enregistrées' if state.questionnaire_dirty(saved_at_render) else ('Questionnaire enregistré' if saved_at_render else 'Questionnaire non enregistré'))
        update_dirty()
        questionnaire = profile.questionnaire
        with ui.tabs().classes('w-full') as tabs:
            labels = [ui.tab(d.id, label=dimension_label(d.id, d.label)) for d in questionnaire.dimensions]
        with ui.tab_panels(tabs, value=labels[0]).classes('w-full'):
            for tab, dimension in zip(labels, questionnaire.dimensions):
                with ui.tab_panel(tab).classes('p-0 flex flex-col gap-4'):
                    for index, question in enumerate(dimension.questions, 1):
                        text(question.text)
                        if question.help:
                            text(question.help, caption=True)
                        marker_id = question.id.upper() if profile.slug == 'baseline' else question.id
                        answer = marked(ui.select(ANSWER_LABELS, label='Votre réponse').props('outlined').classes('w-full')
                                        .bind_value(state.answers, question.id), f'calms_{project.id}_{marker_id}')
                        answer._props['aria-label'] = question.text
                        answer.on_value_change(lambda _: update_dirty())
                        with ui.expansion(f'Ajouter une note à la question {index}'):
                            marked(ui.textarea('Note personnelle (facultative)').props(f'outlined maxlength={MAX_NOTE_LENGTH}').classes('w-full')
                                   .bind_value(state.notes, question.id), f'calms_{project.id}_{marker_id}_note').on_value_change(lambda _: update_dirty())
        button('Enregistrer le questionnaire', shell.action(lambda: shell.session.save_calms(project.id),
               'Questionnaire enregistré. Continuez vers le dépôt ou créez le diagnostic avec ces réponses.'), f'calms_save_{project.id}', primary=True)
        saved = shell.session.store.load_calms(project.id, profile=profile)
        if saved is None:
            text('Aucun questionnaire enregistré pour ce projet.')
            return
        scores = calculate_scores(saved.answers, profile=profile)
        known, total = sum(s.answered for s in scores), len(questionnaire.question_ids)
        text(f"Enregistré le {saved.saved_at.strftime('%d/%m/%Y à %H:%M')} UTC. Les indicateurs ci-dessous utilisent ces réponses enregistrées.", caption=True)
        coverage = f'{known / total * 100:.1f}'.replace('.', ',')
        text(f'{known}/{total} réponses connues ({coverage} %).', key='calms_coverage')
        ui.linear_progress(known / total, show_value=False).props('aria-label="Part des réponses connues enregistrées"')
        with ui.row().classes('w-full gap-4'):
            for score in scores:
                score_badge(dict(asdict(score), coverage=score.coverage))
        with ui.expansion('Comprendre les scores et leurs limites'):
            for score in scores:
                text(score.label + ' : ' + score.interpretation)
            text('Score = somme des réponses connues / (3 × nombre de réponses connues) × 100. Les inconnus sont exclus. Indicateurs déclaratifs internes, sans score global ni certification officielle. Les notes restent locales.', caption=True)
