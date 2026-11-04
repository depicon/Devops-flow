"""Gestion des référentiels publiés ; les écritures passent par ProjectStore."""
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
import re
import unicodedata

from nicegui import run, ui

from devops_flow.models import TECHNICAL_RULE_IDS
from devops_flow.profile_io import export_markdown, parse_markdown
from devops_flow.profiles import EvaluationProfile, MAX_PROFILE_BYTES, baseline_profile, changed_sections
from devops_flow.ui.components import (button, conventions_viewer, import_preview, marked, page_header,
                                         profile_badge, profile_card, section, text)
from devops_flow.ui.upload import MemoryUpload
from devops_flow.ui.copy import dimension_label, rule_title
from devops_flow.ui.components import controls_explainer

STORAGE_ERROR = 'Le stockage local est inaccessible. Réessayez après vérification des droits et de l’espace disponible.'


def link(label, path, key, *, primary=False):
    return button(label, lambda: ui.navigate.to(path), key, primary=primary)


async def _write(shell, operation, success, destination, *, control=None, error=None, dialog=None):
    if shell.busy:
        return
    shell.busy = True
    origin = shell.client.sub_pages_router.current_path
    if control:
        control.disable()
        control.props('loading')
    try:
        result = await run.io_bound(operation)
        if shell.client.is_deleted or shell.client.sub_pages_router.current_path != origin:
            return
        if dialog:
            dialog.close()
        ui.notify(success, type='positive')
        shell.feedback = None
        ui.navigate.to(destination(result) if callable(destination) else destination)
    except (ValueError, sqlite3.Error, OSError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else STORAGE_ERROR
        if not shell.client.is_deleted:
            if error and not error.is_deleted:
                error.set_text(message)
            ui.notify(message, type='negative')
    finally:
        shell.busy = False
        if control and not control.is_deleted:
            control.enable()
            control.props(remove='loading')


def render(shell, page, slug=None):
    if page == 'list':
        render_list(shell)
    elif page == 'new':
        render_form(shell)
    elif page == 'import':
        render_import(shell)
    else:
        profile = shell.session.store.get_profile(slug)
        if profile is None:
            page_header('Profil introuvable', 'Ce référentiel n’est pas disponible.')
            link('Voir les profils', '/profiles', 'profiles_back', primary=True)
            return
        if page == 'edit':
            if slug == 'baseline' or not profile.is_active:
                raise ValueError('Ce profil est protégé ou archivé. Créez un autre profil.')
            render_form(shell, profile)
        else:
            render_detail(shell, profile)


def render_list(shell):
    page_header('Profils d’évaluation', 'Adaptez les questions et les contrôles, puis choisissez une version dans les réglages de chaque projet.')
    with ui.row():
        link('Créer un profil', '/profiles/new', 'profile_new', primary=True)
        link('Importer un Markdown', '/profiles/import', 'profile_import')
    profiles = shell.session.store.list_profiles(include_archived=True)
    if not any(p.slug != 'baseline' for p in profiles):
        text('Aucun profil personnalisé. Le référentiel de base est prêt à être utilisé.', caption=True)
    with ui.row().classes('w-full gap-4 items-stretch'):
        for profile in profiles:
            with profile_card(profile):
                link('Voir le profil', f'/profiles/{profile.slug}', f'profile_open_{profile.slug}')


def render_detail(shell, profile):
    store = shell.session.store
    page_header(profile.name, 'Référentiel publié · les diagnostics conservent la version utilisée.')
    with ui.row():
        link('Tous les profils', '/profiles', 'profiles_back')
        if profile.slug != 'baseline' and profile.is_active:
            link('Publier une nouvelle version', f'/profiles/{profile.slug}/edit', 'profile_edit', primary=True)
    versions = store.list_profile_versions(profile.slug)
    selector = marked(ui.select({p.version: f'Version {p.version}' + (' · courante' if p.is_current else '') for p in versions},
                               value=profile.version, label='Version publiée').props('outlined'), 'profile_version')

    @ui.refreshable
    def version_view():
        selected = store.get_profile(profile.slug, selector.value)
        with profile_card(selected):
            text('Auteur : ' + (selected.author or 'Non renseigné'), caption=True)
            published_at = datetime.fromisoformat(selected.created_at).astimezone(timezone.utc)
            text('Publié le ' + published_at.strftime('%d/%m/%Y à %H:%M') + ' UTC', caption=True)
            button('Exporter en Markdown', lambda: ui.download.content(export_markdown(selected),
                   f'{selected.slug}-v{selected.version}.md', media_type='text/markdown; charset=utf-8'), 'profile_export')
        coverage = f'{selected.min_coverage * 100:.1f}'.replace('.', ',')
        with section('Questions sur les pratiques', f'Seuil d’alerte : {selected.risk_threshold:g} / 100 · Réponses nécessaires : {coverage} %'):
            for dimension in selected.dimensions:
                count = len(dimension.questions)
                with ui.expansion(f'{dimension_label(dimension.id, dimension.label)} · {count} question' + ('s' if count > 1 else '')):
                    for question in dimension.questions:
                        text(question.text)
                        if question.help:
                            text(question.help, caption=True)
        with section('Contrôles du dépôt'):
            if not selected.rules:
                text('Aucun contrôle du dépôt activé.')
            for rule in selected.rules:
                text(rule_title(rule.id))
            controls_explainer(selected)
        with section('Conventions'):
            conventions_viewer(selected.conventions)
    version_view()
    selector.on_value_change(lambda: version_view.refresh())
    with section('Diagnostics liés'):
        diagnostics = store.profile_diagnostics(profile.slug)
        if not diagnostics:
            text('Aucun diagnostic ne référence encore ce profil.')
        for diagnostic in diagnostics:
            with ui.row().classes('w-full items-center justify-between'):
                text(f'{diagnostic.created_at.strftime("%d/%m/%Y %H:%M")} UTC · {diagnostic.profile_ref}')
                def open_diagnostic(item=diagnostic):
                    shell.session.open_project(item.project_id)
                    shell.navigate('Historique / Rapports', item.id)
                button('Consulter le diagnostic', open_diagnostic, f'profile_diagnostic_{diagnostic.id}')
    if profile.slug != 'baseline' and profile.is_active:
        with ui.dialog() as dialog, ui.card().classes('app-card'):
            text('Archiver ce profil ?')
            text('Le profil sera archivé. Un profil assigné à un projet ou utilisé par un diagnostic ne peut pas être archivé.', caption=True)
            error = text('').classes('feedback error').props('role=alert')
            error.bind_visibility_from(error, 'text', bool)
            with ui.row():
                confirm = button('Confirmer l’archivage', lambda: _write(shell, lambda: store.archive_profile(profile.slug),
                                 'Profil archivé', '/profiles', control=confirm, error=error, dialog=dialog), 'profile_delete_confirm')
                button('Annuler', dialog.close, 'profile_delete_cancel')
        button('Archiver le profil', dialog.open, 'profile_delete').props('color=negative')


def render_form(shell, original=None):
    data = (original or baseline_profile()).to_dict()
    if original is None:
        data.update(slug='', name='', description='', author='', conventions='')
        data['calms']['questions'] = {d: [{'id': d.lower()+'1', 'text':'', 'help':''}] for d in 'CALMS'}
    page_header('Nouvelle version' if original else 'Créer un profil',
                'Le contenu publié sera immuable. Les projets gardent leur assignation jusqu’à une modification explicite.')
    link('Retour aux profils', '/profiles', 'profiles_back')
    with section('Informations du profil'):
        generated = {'slug': ''}
        def suggest(event):
            if not original and data['slug'] in ('', generated['slug']):
                value = unicodedata.normalize('NFKD', event.value or '').encode('ascii', 'ignore').decode().lower()
                value = re.sub('[^a-z0-9]+', '-', value).strip('-')[:64].rstrip('-') or 'profil'
                if value in ('baseline', 'new', 'import'):
                    value = 'profil-' + value
                data['slug'] = generated['slug'] = value
        for field, label, maximum in [('name','Nom du profil',120),('description','Description (facultative)',2000)]:
            element = ui.textarea(label) if field == 'description' else ui.input(label)
            marked(element.props(f'outlined maxlength={maximum}').classes('w-full').bind_value(data,field), 'profile_'+field)
            if field == 'name':
                element.on_value_change(suggest)
        with ui.expansion('Informations avancées du profil'):
            text('L’identifiant technique est proposé à partir du nom. Il sert à l’import et aux liens du profil.', caption=True)
            slug = marked(ui.input('Identifiant technique', value=data['slug']).props('outlined maxlength=64').classes('w-full').bind_value(data,'slug'), 'profile_slug')
            if original:
                slug.disable()
            marked(ui.input('Auteur (facultatif)').props('outlined maxlength=120').classes('w-full').bind_value(data,'author'), 'profile_author')
    with section('Questions sur les pratiques', 'De 1 à 10 questions par aspect évalué. Écrivez une question claire et une aide si nécessaire.'):
        with ui.tabs().classes('w-full') as tabs:
            tab_items = {d: ui.tab(d,label=dimension_label(d, dimension.label)) for d,dimension in zip('CALMS',baseline_profile().dimensions)}
        with ui.tab_panels(tabs,value=tab_items['C']).classes('w-full'):
            for dimension in 'CALMS':
                with ui.tab_panel(tab_items[dimension]).classes('p-0 flex flex-col gap-4'):
                    _question_editor(data, dimension)
    with ui.expansion('Options avancées : interprétation des réponses'):
        text('Ces seuils sont des conventions internes, pas des niveaux officiels. Un score sous le seuil déclenche une alerte seulement si la part de réponses connues est suffisante.', caption=True)
        marked(ui.number('Seuil d’alerte sur 100',min=0,max=100).props('outlined').bind_value(data['calms'],'risk_threshold'), 'profile_risk_threshold')
        marked(ui.number('Part minimale de réponses connues (%)',value=data['calms']['min_coverage']*100,min=0,max=100,step=1,
                         on_change=lambda event: data['calms'].__setitem__('min_coverage', event.value / 100 if event.value is not None else None))
               .props('outlined').classes('w-full'), 'profile_min_coverage')
    configured = {r['id']:r for r in data['rules']}
    rule_inputs = {rid: {'enabled':rid in configured, 'impact':configured.get(rid,{}).get('impact'),
                         'effort':configured.get(rid,{}).get('effort')} for rid in TECHNICAL_RULE_IDS}
    with section('Contrôles du dépôt', 'Activez les contrôles utiles. Un contrôle désactivé ne produit ni constat ni action.'):
        for rid, values in rule_inputs.items():
            marked(ui.checkbox(rule_title(rid)).bind_value(values,'enabled'), f'profile_rule_{rid}')
            with ui.expansion('Options de priorité · ' + rule_title(rid)):
                text('Impact et effort vont de 1 (faible) à 3 (élevé). Laissez vide pour garder les valeurs par défaut. La priorité est le rapport impact / effort.', caption=True)
                for field,label in [('impact','Impact'),('effort','Effort')]:
                    marked(ui.number(label,min=1,max=3,step=1,precision=0).props('outlined clearable')
                           .bind_value(values,field).bind_enabled_from(values,'enabled'), f'profile_rule_{rid}_{field}')
                text('Référence interne : ' + rid, caption=True)
    with section('Conventions', 'Ce texte publié est inclus dans les rapports. Images et contenu HTML actif ne sont pas rendus.'):
        conventions = marked(ui.textarea('Conventions (Markdown)').props('outlined').classes('w-full').bind_value(data,'conventions'), 'profile_conventions')
        with ui.expansion('Aperçu des conventions',value=True):
            preview = conventions_viewer(data['conventions'])
        conventions.on_value_change(lambda event: preview.set_content(event.value or 'Aucune convention renseignée.'))
    error = marked(text('').classes('feedback error').props('role=alert'),'profile_form_error')
    error.bind_visibility_from(error, 'text', bool)

    async def submit():
        try:
            data['rules'] = []
            for rid, values in rule_inputs.items():
                if values['enabled']:
                    entry = {'id':rid}
                    for field in ('impact','effort'):
                        value = values[field]
                        if value is not None:
                            entry[field] = int(value) if type(value) in (int,float) and float(value).is_integer() else value
                    data['rules'].append(entry)
            candidate = EvaluationProfile.from_dict(data)
            export_markdown(candidate)  # Le profil publié doit rester exportable/importable.
            if original:
                sections = changed_sections(original,candidate)
                with ui.dialog() as dialog, ui.card().classes('app-card'):
                    text(f'Publier la version {original.version+1} ?')
                    text('Sections modifiées : ' + (', '.join(sections) or 'aucune'))
                    text('Les projets et diagnostics existants conservent leur version.',caption=True)
                    with ui.row():
                        confirm = button('Confirmer la publication',lambda: _write(shell,
                            lambda: shell.session.store.publish_profile(candidate,expected_version=original.version),
                            'Nouvelle version publiée',f'/profiles/{candidate.slug}',control=confirm,error=error,dialog=dialog), 'profile_publish_confirm')
                        button('Annuler',dialog.close,'profile_publish_cancel')
                dialog.open()
            else:
                await _write(shell,lambda: shell.session.store.publish_profile(candidate),'Profil créé',
                             f'/profiles/{candidate.slug}',control=publish,error=error)
        except ValueError as exc:
            error.set_text(str(exc))
            ui.notify(str(exc),type='negative')
    publish = button(f'Publier la version {original.version+1 if original else 1}',submit,'profile_publish',primary=True)


def render_import(shell):
    page_header('Importer un profil', 'Choisissez un fichier Markdown UTF‑8 de 256 Kio maximum, puis vérifiez son aperçu.')
    link('Retour aux profils','/profiles','profiles_back')
    state = {'preview':None,'revision':0}
    error = marked(text('').classes('feedback error').props('role=alert'),'profile_import_error')
    error.bind_visibility_from(error, 'text', bool)
    preview_box = ui.column().classes('w-full')

    def begin():
        state['revision'] += 1
        state['preview'] = None
        create.disable()
        preview_box.clear()
        error.set_text('')
        return state['revision']

    def rejected(ticket=None):
        if ticket is not None and ticket != state['revision']:
            return
        begin()
        error.set_text('Import refusé : choisissez un seul Markdown de 256 Kio maximum.')

    async def received(file,ticket):
        try:
            parsed = await run.io_bound(parse_markdown,await file.read())
        except ValueError as exc:
            if ticket == state['revision'] and not error.is_deleted:
                error.set_text(str(exc))
            return
        if ticket != state['revision'] or preview_box.is_deleted:
            return
        state['preview'] = parsed
        with preview_box:
            marked(import_preview(parsed),'profile_import_preview')
        create.enable()

    upload = MemoryUpload(file_limit=MAX_PROFILE_BYTES,extension='.md',
                          rejected_message='Import refusé : choisissez un Markdown de 256 Kio maximum.',
                          label='Choisir un Markdown',auto_upload=True,on_start=begin,on_file=received,
                          on_error=rejected,on_rejected=lambda: rejected())
    marked(upload.props('accept=.md').classes('w-full'),'profile_upload')
    upload.on('removed',lambda: begin())

    async def create_profile():
        parsed = state['preview']
        if parsed is None:
            return
        profile = parsed.profile
        existing = shell.session.store.get_profile(profile.slug)
        if existing is None:
            await _write(shell,lambda: shell.session.store.publish_profile(profile),'Profil créé',
                         f'/profiles/{profile.slug}',control=create,error=error)
            return
        with ui.dialog() as dialog, ui.card().classes('app-card'):
            text('Cet identifiant de profil existe déjà.')
            if existing.is_active and existing.slug != 'baseline':
                confirm = button('Nouvelle version',lambda: _write(shell,
                    lambda: shell.session.store.publish_profile(profile,expected_version=existing.version),
                    'Nouvelle version publiée',f'/profiles/{profile.slug}',control=confirm,error=error,dialog=dialog), 'profile_import_version')
            else:
                text('Ce profil est protégé ou archivé. Choisissez un autre identifiant.',caption=True)
            alternate = marked(ui.input('Nouvel identifiant technique').props('outlined maxlength=64'),'profile_import_slug')
            async def change_slug():
                try:
                    candidate = EvaluationProfile.from_dict(replace(profile,slug=alternate.value or '').to_dict())
                except ValueError as exc:
                    error.set_text(str(exc))
                    ui.notify(str(exc),type='negative')
                    return
                await _write(shell,lambda: shell.session.store.publish_profile(candidate),'Profil créé',
                             f'/profiles/{candidate.slug}',control=change,error=error,dialog=dialog)
            change = button('Créer sous cet identifiant',change_slug,'profile_import_change_slug')
            button('Annuler',dialog.close,'profile_import_dialog_cancel')
        dialog.open()
    create = button('Créer le profil',create_profile,'profile_import_create',primary=True)
    create.disable()
    link('Annuler','/profiles','profile_import_cancel')


def _question_editor(data, dimension):
    @ui.refreshable
    def questions():
        d = dimension
        entries = data['calms']['questions'][d]
        for index, question in enumerate(entries):
            with ui.card().classes('app-card'):
                text(f'Question {index+1}', caption=True)
                for field,label,maximum in [('text','Question',1000),('help','Aide facultative',2000)]:
                    element = ui.textarea(label) if field == 'help' else ui.input(label)
                    marked(element.props(f'outlined maxlength={maximum}').classes('w-full').bind_value(question,field), f'profile_{d}_{index}_{field}')
                with ui.expansion('Identifiant technique de la question'):
                    text('Généré automatiquement. Modifiez-le uniquement pour organiser un référentiel importé ; il doit être unique.', caption=True)
                    marked(ui.input('Identifiant').props('outlined maxlength=64').classes('w-full').bind_value(question,'id'), f'profile_{d}_{index}_id')
                def remove(entry=question, items=entries, refresh=questions):
                    if len(items)>1:
                        items.remove(entry)
                        refresh.refresh()
                delete = button('Supprimer la question',remove,f'profile_{d}_{index}_delete').props('icon=delete')
                if len(entries)==1:
                    delete.disable()
        def add(items=entries, key=d, refresh=questions):
            if len(items)<10:
                ids = {q['id'] for group in data['calms']['questions'].values() for q in group}
                number = next(n for n in range(1,51) if f'{key.lower()}{n}' not in ids)
                items.append({'id':f'{key.lower()}{number}','text':'','help':''})
                refresh.refresh()
        add_button = button('Ajouter une question',add,f'profile_{d}_add')
        if len(entries)>=10:
            add_button.disable()
    questions()
