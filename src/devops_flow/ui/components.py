"""Composants NiceGUI partagés, sans accès SQL ni calcul de score."""
from contextlib import contextmanager
from nicegui import ui
from devops_flow.ui.copy import dimension_label, evidence_label, finding_title, rule_title, RULE_HELP

FINDING_LABELS = {'observed': 'Observé', 'risk': 'Point à vérifier', 'not_observed': 'Non observé',
                  'unknown': 'À confirmer', 'invalid': 'Non analysable', 'not_applicable': 'Non applicable'}


def marked(element, key):
    element.mark(key)
    element._props['data-testid'] = key
    return element


def text(value, *, caption=False, key=None):
    element = ui.label(str(value)).classes('literal caption' if caption else 'literal')
    return marked(element, key) if key else element


def heading(value, level=2):
    return ui.label(value).props(f'role=heading aria-level={level}').classes('page-heading' if level == 1 else 'section-heading')


def page_header(title, subtitle, project=None):
    heading(title, 1)
    text(subtitle, caption=True)


@contextmanager
def section(title, subtitle=None):
    with ui.card().classes('app-card'):
        heading(title)
        if subtitle:
            text(subtitle, caption=True)
        yield


def button(label, callback, key, *, primary=False):
    return marked(ui.button(label, on_click=callback).props('unelevated' if primary else 'outline', remove='color')
                  .classes('app-primary' if primary else 'app-secondary'), key)


def navigation_button(shell, label, page, key, *, diagnostic_id=None, primary=False):
    return button(label, lambda: shell.navigate(page, diagnostic_id), key, primary=primary)


def empty_state(shell, message, key):
    with section('Commencez votre évaluation'):
        text(message)
        navigation_button(shell, 'Ouvrir les projets', 'Projets', key, primary=True)


def kpi_card(label, value):
    with ui.column().classes('kpi'):
        text(label, caption=True)
        text(value).classes('kpi-value')


def score_badge(score):
    value = 'Indéterminé' if score['score'] is None else f"{score['score']:.1f}".replace('.', ',') + ' / 100'
    with ui.column().classes('kpi'):
        text(dimension_label(score.get('id', ''), score['label']), caption=True)
        text(value).classes('kpi-value')
        coverage = f"{score['answered'] / score['total'] * 100:.1f}".replace('.', ',')
        text(f"{score['answered']}/{score['total']} réponses connues ({coverage} %)", caption=True)


def finding_card(finding, *, recommendations=(), questions=None):
    with ui.expansion(f"{rule_title(finding['rule_id'])} — {FINDING_LABELS[finding['status']]}") as card:
        with ui.column().classes('w-full gap-3'):
            text('Ce qui est constaté', caption=True)
            text(finding['explanation'])
            if finding['rule_id'] in RULE_HELP:
                text('Pourquoi ce contrôle ?', caption=True)
                text(RULE_HELP[finding['rule_id']][0])
            text('Éléments examinés · ' + finding['source'], caption=True)
            for evidence in finding['evidence']:
                question = (questions or {}).get(evidence.get('question_id'))
                if question:
                    from devops_flow.ui.copy import ANSWER_LABELS
                    text(question + ' · ' + ANSWER_LABELS.get(evidence['value'], str(evidence['value'])))
                else:
                    text(evidence_label(evidence))
            text('Limites de la conclusion', caption=True)
            text(finding['limitation'], caption=True)
            linked = [r for r in recommendations if r['id'] in finding.get('recommendation_ids', [])]
            text('Quoi faire ensuite ?', caption=True)
            if linked:
                for rec in linked:
                    text(rec['title'])
            else:
                text('Consultez les actions proposées dans les résultats du diagnostic.' if finding['status'] in ('risk','unknown','invalid','not_observed')
                     else 'Aucune action automatique. Ce résultat reste limité au périmètre examiné.', caption=True)
            with ui.expansion('Détails techniques'):
                text('Référence du contrôle : ' + finding['rule_id'], caption=True)
                text(f"Gravité : {finding.get('severity') or 'non établie'}", caption=True)
                for evidence in finding['evidence']:
                    text(evidence.get('question_id') or evidence['code'], caption=True)
                if 'id' in finding:
                    text(f"ID : {finding['id']} · Catégorie : {finding['category']}", caption=True)
                    text('Références des actions : ' + (', '.join(finding['recommendation_ids']) or 'aucune'), caption=True)
    return card


def recommendation_card(rec):
    with section(rec['title']):
        text(rec['description'])
        text(f"Impact {rec['impact']} / effort {rec['effort']} = priorité {rec['priority']:g}", caption=True)
        with ui.expansion('Pourquoi cette recommandation ?'):
            text(rec['justification'])
            text('Constats liés : ' + (rule_title(rec['rule_id']) if rec.get('rule_id') else ', '.join(finding_title(fid) for fid in rec['source_finding_ids'])))
            text(f"Référence : {rec['id']} · Gravité : {rec['severity'] or 'non établie'}", caption=True)


def profile_card(profile):
    with ui.card().classes('app-card') as card:
        heading(profile.name)
        profile_badge(profile)
        text(profile.description or 'Aucune description renseignée.', caption=True)
        with ui.row():
            text('Actif' if profile.is_active else 'Archivé').classes('feedback success' if profile.is_active else 'feedback')
            if profile.slug == 'baseline':
                text('Référentiel de base · protégé', caption=True)
            if profile.is_current:
                text('Version courante', caption=True)
    return card


def profile_selector(profiles, current_slug, on_change):
    options = {p.slug: f'{p.name} · version {p.version}' for p in profiles if p.is_active}
    return ui.select(options, value=current_slug, label="Profil d’évaluation", on_change=on_change).props('outlined').classes('w-full')


def profile_badge(profile):
    if isinstance(profile, dict):
        slug, version = profile['slug'], profile['version']
        name = profile.get('name', 'Référentiel de base' if slug == 'baseline' else slug)
    else:
        slug, version = profile.slug, profile.version
        name = profile.name
    return text(f'{name} · version {version}').classes('profile-badge')


def conventions_viewer(conventions_md):
    from devops_flow.ui.markdown import safe_markdown
    return safe_markdown(conventions_md or 'Aucune convention renseignée.')


def import_preview(parsed_profile):
    profile = parsed_profile.profile
    with ui.expansion('Aperçu du profil importé', value=True).classes('w-full') as expansion:
        text(profile.name)
        profile_badge(profile)
        text(profile.description, caption=True)
        text('Auteur : ' + (profile.author or 'Non renseigné'), caption=True)
        text('Date source : ' + profile.created_at, caption=True)
        with ui.row().classes('w-full gap-4'):
            for dimension in profile.dimensions:
                kpi_card(dimension_label(dimension.id, dimension.label), f'{len(dimension.questions)} questions')
        text(f'Seuil d’alerte : {profile.risk_threshold:g} / 100 · Réponses nécessaires : {profile.min_coverage * 100:g} %')
        text('Contrôles activés : ' + (', '.join(rule_title(rid) for rid in profile.rule_ids) or 'aucun'))
        conventions_viewer(profile.conventions)
    return expansion


def controls_explainer(profile, *, key='controls_count'):
    with ui.expansion('Comprendre les contrôles'):
        count = len(profile.rule_ids)
        text(f'{count} contrôle' + (' activé' if count == 1 else 's activés') + ' sur 6 disponibles.', key=key)
        text('Trois contrôles Dockerfile, un Compose et deux GitLab CI : ces nombres reflètent le périmètre implémenté, pas une norme ni une évaluation exhaustive.', caption=True)
        text('Le profil choisit les contrôles utilisés. Un contrôle désactivé ne produit pas de constat. Un contrôle non applicable reste distinct d’un résultat filtré.', caption=True)
        for rid, (purpose, limitation) in RULE_HELP.items():
            with ui.expansion(rule_title(rid) + (' · activé' if rid in profile.rule_ids else ' · désactivé')):
                text(purpose)
                text(limitation, caption=True)
                text('Référence interne : ' + rid, caption=True)
        text('Les seuils et la priorité impact / effort sont des conventions internes. Leur justification méthodologique reste ouverte ; ce ne sont pas des niveaux officiels.', caption=True)
        text('Les documentations suivantes expliquent les pratiques visées. Elles ne valident ni notre sélection de contrôles ni notre barème.', caption=True)
        for label, url in [('Docker : construction et versions d’images', 'https://docs.docker.com/build/building/best-practices/'),
                           ('Compose : privilèges des services', 'https://docs.docker.com/reference/compose-file/services/'),
                           ('GitLab : configuration des tâches', 'https://docs.gitlab.com/ci/yaml/')]:
            ui.link(label, url, new_tab=True).props('rel="noopener noreferrer"')
