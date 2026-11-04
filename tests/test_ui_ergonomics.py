"""Parcours de la refonte : conservation de saisie, sources, versions et contexte."""
from pathlib import Path

import pytest
from devops_flow.diagnostic import build_diagnostic
from devops_flow.profiles import EvaluationProfile
from test_profiles import profile_data

FIXTURES = Path(__file__).parent / 'fixtures'


async def test_creation_is_separate_and_header_links_are_available(gui):
    await gui.open()
    assert not gui.element('project_settings').enabled
    assert not any('project_name' in e._markers for e in gui.user.client.elements.values())
    await gui.click('project_new')
    assert gui.user.client.sub_pages_router.current_path == '/projects/new'
    await gui.set('project_name', 'Premier projet')
    await gui.click('project_create')
    assert gui.user.client.sub_pages_router.current_path == '/'
    assert gui.element('active_project').text == 'Premier projet'
    assert gui.element('project_settings').enabled
    await gui.click('project_settings')
    assert gui.element('project_edit_name').value == 'Premier projet'


async def test_steps_theme_and_navigation_preserve_unsaved_answers(gui, store):
    project = store.create_project('Saisie à reprendre')
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.set(f'calms_{project.id}_C1', 3)
    await gui.set(f'calms_{project.id}_C1_note', 'À conserver')
    assert gui.element('questionnaire_dirty').text == 'Modifications non enregistrées'
    await gui.set('diagnostic_steps', 'repository')
    await gui.local(project.id, FIXTURES/'healthy_project')
    scan = gui.session.project().scan
    await gui.set('theme_mode', 'dark')
    assert gui.shell.dark_mode.value is True
    await gui.set('diagnostic_steps', 'questionnaire')
    assert gui.element(f'calms_{project.id}_C1').value == 3
    assert gui.element(f'calms_{project.id}_C1_note').value == 'À conserver'
    assert gui.session.project().scan == scan
    assert store.load_calms(project.id) is None
    await gui.click(f'calms_save_{project.id}')
    assert gui.element('questionnaire_dirty').text == 'Questionnaire enregistré'
    assert store.load_calms(project.id).notes['C1'] == 'À conserver'


@pytest.mark.parametrize('choice', ['calms', 'scan', 'both'])
async def test_source_choices_create_only_the_selected_inputs(gui, store, choice):
    project = store.create_project('Sources')
    store.save_calms(project.id, {})
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.local(project.id, FIXTURES/'healthy_project')
    # Changement explicite depuis un autre choix, y compris pour « les deux ».
    await gui.set('diagnostic_sources', 'scan' if choice != 'scan' else 'calms')
    await gui.set('diagnostic_sources', choice)
    assert gui.element(f'diagnostic_finalize_{project.id}').enabled
    await gui.click(f'diagnostic_finalize_{project.id}')
    inputs = store.latest_diagnostic(project.id).snapshot.to_dict()['inputs']
    assert (inputs['calms'] is not None) == (choice in ('calms', 'both'))
    assert (inputs['scan'] is not None) == (choice in ('scan', 'both'))


async def test_missing_selected_source_explains_disabled_creation(gui, store):
    project = store.create_project('Sources manquantes')
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.set('diagnostic_sources', 'scan')
    assert not gui.element(f'diagnostic_finalize_{project.id}').enabled
    assert 'indisponible' in gui.element(f'diagnostic_readiness_{project.id}').text
    await gui.local(project.id, FIXTURES/'healthy_project')
    assert gui.element(f'diagnostic_finalize_{project.id}').enabled
    await gui.set(f'scan_path_{project.id}', '/chemin/fictif')
    # L'état métier invalide immédiatement l'ancien résultat.
    assert gui.session.project().scan is None
    await gui.click(f'scan_run_{project.id}')
    assert not gui.element(f'diagnostic_finalize_{project.id}').enabled
    assert store.latest_diagnostic(project.id) is None


async def test_project_rename_preserves_unsaved_form_and_scan(gui, store):
    project = store.create_project('Avant')
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.set(f'calms_{project.id}_C1', 2)
    await gui.local(project.id, FIXTURES/'healthy_project')
    scan = gui.session.project().scan
    await gui.click('project_settings')
    await gui.set('project_edit_name', 'Après')
    await gui.click('project_update')
    assert store.get_project(project.id).name == 'Après'
    assert gui.element('active_project').text == 'Après'
    await gui.click('settings_diagnostic')
    assert gui.element(f'calms_{project.id}_C1').value == 2
    assert gui.session.project().scan == scan


async def test_profile_change_warns_before_discarding_unsaved_questionnaire(gui, store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Profil')
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.set(f'calms_{project.id}_C1', 3)
    await gui.click('project_settings')
    await gui.set('project_edit_profile', profile.slug)
    await gui.click('project_update')
    assert store.project_profile(project.id).slug == 'baseline'
    await gui.click('settings_cancel_change')
    assert gui.session.project().diagnostic_step == 'questionnaire'
    assert gui.element(f'calms_{project.id}_C1').value == 3
    await gui.click('project_settings')
    await gui.set('project_edit_profile', profile.slug)
    await gui.click('project_update')
    await gui.click('settings_confirm_change')
    assert store.project_profile(project.id).slug == profile.slug
    await gui.click('settings_diagnostic')
    assert gui.session.project().answers[profile.question_ids[0]] == 'unknown'


async def test_coverage_percentage_roundtrip_and_existing_versions_are_pinned(gui, store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Version fixe', profile_slug=profile.slug)
    saved = store.save_diagnostic(project.id, 'ancien', build_diagnostic(answers={}, profile=profile))
    await gui.user.open('/profiles/' + profile.slug + '/edit')
    assert gui.element('profile_min_coverage').value == pytest.approx(profile.min_coverage*100)
    await gui.set('profile_min_coverage', 75)
    await gui.click('profile_publish')
    await gui.click('profile_publish_confirm')
    assert store.get_profile(profile.slug).min_coverage == .75
    assert store.project_profile(project.id).version == 1
    assert store.get_diagnostic(project.id, saved.id).snapshot.to_json() == saved.snapshot.to_json()


async def test_profile_name_generates_identifier_without_requiring_technical_field(gui, store):
    await gui.user.open('/profiles/new')
    await gui.set('profile_name', 'Équipe Plateforme')
    for dimension in 'CALMS':
        await gui.set(f'profile_{dimension}_0_text', 'Une pratique claire ?')
    await gui.click('profile_publish')
    assert store.get_profile('equipe-plateforme').name == 'Équipe Plateforme'


async def test_project_context_survives_secondary_routes_and_direct_settings(gui, store):
    project = store.create_project('Contexte permanent')
    await gui.user.open('/projects/' + project.id + '/settings')
    assert gui.element('active_project').text == project.name
    await gui.click('nav_profiles')
    assert gui.element('active_project').text == project.name
    await gui.click('project_settings')
    assert gui.element('active_project').text == project.name
    await gui.click('settings_diagnostic')
    assert gui.element('nav_1')._props['aria-current'] == 'page'


async def test_controls_counts_and_titles_reflect_profile_not_filter(gui, store):
    data = profile_data()
    data['rules'] = [{'id': 'DOC-002'}]
    profile = store.publish_profile(EvaluationProfile.from_dict(data))
    project = store.create_project('Un contrôle', profile_slug=profile.slug)
    await gui.open(project.id, 'Diagnostic guidé')
    assert gui.element('controls_count').text == '1 contrôle activé sur 6 disponibles.'
    await gui.local(project.id, FIXTURES/'incomplete_project')
    assert any('Utilisation explicite du tag latest — Point à vérifier' == e._props.get('label')
               for e in gui.user.client.elements.values())
    await gui.click(f'diagnostic_finalize_{project.id}')
    snapshot = store.latest_diagnostic(project.id).snapshot.to_dict()
    assert {f['rule_id'] for f in snapshot['findings'] if not f['rule_id'].startswith('CAL-')} == {'DOC-002'}


async def test_missing_direct_project_settings_is_neutral(gui):
    await gui.user.open('/projects/inconnu/settings')
    assert 'Le projet demandé n’est plus disponible' in gui.element('data_error').text
    assert gui.session.active_project_id is None


def test_readable_flow_keeps_statuses_edges_and_export_references():
    from devops_flow.diagram import build_flow, snapshot_to_mermaid
    from devops_flow.ui.flow import readable_mermaid
    snapshot = build_diagnostic(answers={})
    before = snapshot.to_json()
    exported = snapshot_to_mermaid(snapshot)
    display = readable_mermaid(build_flow(snapshot))
    assert 'DOC-001' in exported and 'DOC-001' not in display
    assert 'CALMS :' not in display
    def topology(source):
        return [line for line in source.splitlines() if '-->' in line or '-.->' in line or line.strip().startswith('class ')]
    assert topology(display) == topology(exported)
    assert snapshot.to_json() == before
    assert snapshot_to_mermaid(snapshot) == exported
