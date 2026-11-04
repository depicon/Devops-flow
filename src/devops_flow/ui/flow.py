"""Vue de flux partagée ; sélection et preuves accessibles sans interaction SVG."""
from nicegui import ui
from dataclasses import asdict
from html import escape

from devops_flow.diagram import STATUS_LABELS, build_flow, render_mermaid
from devops_flow.ui.components import button, marked, section, text
from devops_flow.ui.theme import DIAGRAM_COLORS
from devops_flow.ui.copy import DIMENSION_LABELS, evidence_label, rule_title


def readable_mermaid(flow, *, highlighted_status=None):
    """Libellés UI ; topologie, statuts et Mermaid des exports restent inchangés."""
    source = render_mermaid(flow, highlighted_status=highlighted_status)
    short = {'C':'Culture', 'A':'Automatisation', 'L':'Amélioration', 'M':'Mesure', 'S':'Partage'}
    declarations = {}
    for node in flow.nodes:
        labels = [escape(node.label, quote=True), STATUS_LABELS[node.status]]
        if node.dimensions:
            labels.append(' · '.join(short[d] for d in node.dimensions))
        declarations[node.id] = node.id + '["' + '<br/>'.join(labels) + '"]'
    lines = []
    for line in source.splitlines():
        if line.strip().startswith('style culture '):
            # Mermaid transforme ce style en !important inline ; laisser le thème
            # applicatif gérer cette surface pour les deux apparences.
            continue
        identifier = line.lstrip().split('["', 1)[0]
        if identifier in declarations:
            line = line[:len(line)-len(line.lstrip())] + declarations[identifier]
        lines.append(line)
    return '\n'.join(lines) + '\n'


def render_flow(snapshot, key, on_open_finding):
    flow = build_flow(snapshot)
    nodes = {n.id: n for n in flow.nodes}
    selected = {'node': 'code', 'status': None}
    with section('Flux DevOps', 'Repérez les points à vérifier, du code jusqu’aux retours de l’équipe.'):
        text('Faites défiler le flux horizontalement. Choisissez une étape dans le schéma ou dans la liste pour vérifier ses sources.', caption=True)
        with ui.element('div').classes('flow-layout'):
            with ui.column().classes('flow-graphic'):
                with ui.element('div').classes('flow-legend').props('aria-label="Légende interactive des statuts"'):
                    legend = {}

                    def highlight(status):
                        selected['status'] = None if selected['status'] == status else status
                        for value, control in legend.items():
                            control.props('aria-pressed=' + str(value == selected['status']).lower())
                        graphic.refresh()
                        matching.refresh()

                    for status, label in STATUS_LABELS.items():
                        fill, color, _ = DIAGRAM_COLORS[status]
                        control = button(label, lambda s=status: highlight(s), f'flow_legend_{key}_{status}')
                        control.classes(remove='app-secondary').style(f'background:{fill};color:{color}').props('unelevated aria-pressed=false', remove='outline color')
                        legend[status] = control

                @ui.refreshable
                def matching():
                    status = selected['status']
                    if status:
                        matches = [n for n in flow.nodes if n.status == status]
                        text(f'{STATUS_LABELS[status]} : ' + (', '.join(n.label for n in matches) or 'aucune étape'),
                             key=f'flow_matches_{key}').props('role=status')
                matching()

                def choose(node_id):
                    if node_id in nodes:
                        selected['node'] = node_id
                        selector.value = node_id
                        trace.refresh()

                @ui.refreshable
                def graphic():
                    error_box = ui.column().classes('w-full')
                    error_box.set_visibility(False)
                    with ui.element('div').classes('flow-scroll').props('tabindex=0 aria-label="Diagramme DevOps, défilement horizontal"'):
                        chart = marked(ui.mermaid(readable_mermaid(flow, highlighted_status=selected['status']),
                                                 config={'securityLevel': 'strict', 'fontFamily': 'system-ui, sans-serif',
                                                         'themeVariables': {'fontSize': '16px'},
                                                         'flowchart': {'useMaxWidth': False, 'htmlLabels': False}},
                                                 on_node_click=lambda event: choose(event.node_id.removeprefix('flowchart-')))
                                       .classes('flow-mermaid'), f'flow_diagram_{key}')

                    def failed():
                        chart.set_visibility(False)
                        error_box.set_visibility(True)
                        error_box.clear()
                        with error_box:
                            text('Le diagramme ne peut pas être affiché. Les sources restent disponibles dans le panneau.',
                                 key=f'flow_error_{key}').classes('feedback error').props('role=alert')
                            button('Réessayer le rendu', graphic.refresh, f'flow_retry_{key}')
                    chart.on('error', failed)
                graphic()
                with ui.expansion('Dimensions CALMS et limites du flux'):
                    text('Culture englobe tout le flux. Les badges indiquent les dimensions associées aux étapes : ' +
                         ', '.join(f'{k} — {v}' for k, v in DIMENSION_LABELS.items()), caption=True)
                    text(f"Culture · {STATUS_LABELS[flow.culture.status]}")
                    button('Voir le constat Culture', lambda: on_open_finding(flow.culture.finding_id), f'flow_culture_{key}')
                    text('Les flèches décrivent le flux attendu, pas des dépendances ou des exécutions vérifiées. '
                         'Un service déclaré dans Compose ne prouve ni son démarrage ni une dépendance externe.', caption=True)

            with marked(ui.column().classes('flow-trace'), f'flow_trace_{key}'):
                text('Comprendre cette étape').props('role=heading aria-level=3')
                selector = marked(ui.select({n.id: n.label for n in flow.nodes}, value='code', label='Étape ou service à vérifier',
                                            on_change=lambda event: choose(event.value)).props('outlined').classes('w-full'),
                                  f'flow_node_{key}')

                @ui.refreshable
                def trace():
                    node = nodes[selected['node']]
                    questions = {variant: q['text'] for entries in snapshot.to_dict().get('profile', {}).get('calms', {}).get('questions', {}).values()
                                 for q in entries for variant in (q['id'], q['id'].upper())}
                    text(f'{node.label} · {STATUS_LABELS[node.status]}', key=f'flow_status_{key}').props('role=status')
                    for index, annotation in enumerate(node.annotations):
                        title = rule_title(annotation.rule_id) if annotation.rule_id else 'Pratiques déclarées' if annotation.question_id else 'Profil utilisé'
                        with ui.expansion(title + ' · ' + STATUS_LABELS[annotation.status], value=index == 0).classes('w-full'):
                            text('Origine : ' + annotation.source, caption=True)
                            for proof in annotation.evidence:
                                if proof.question_id and proof.question_id in questions:
                                    from devops_flow.ui.copy import ANSWER_LABELS
                                    text(questions[proof.question_id])
                                    text(ANSWER_LABELS.get(proof.value, str(proof.value)), caption=True)
                                else:
                                    text(evidence_label(asdict(proof)), caption=True)
                            if annotation.profile_ref:
                                with ui.expansion('Référence au profil figé'):
                                    text(annotation.profile_ref)
                                    data = snapshot.to_dict()['profile']
                                    text('Contrôles activés : ' + (', '.join(rule_title(r['id']) for r in data['rules']) or 'aucun'), caption=True)
                                    text('Questions : ' + ', '.join(q['id'] for entries in data['calms']['questions'].values() for q in entries), caption=True)
                            if annotation.limitation:
                                text(annotation.limitation, caption=True)
                            if annotation.finding_id:
                                with ui.expansion('Détails techniques'):
                                    text(annotation.finding_id, caption=True)
                                    text(annotation.reason, caption=True)
                                button('Voir le constat détaillé', lambda fid=annotation.finding_id: on_open_finding(fid),
                                       f'flow_source_{key}_{index}')
                trace()
