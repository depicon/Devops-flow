import json
import sqlite3
from nicegui import ui


async def test_no_questionnaire_until_a_project_is_selected(gui):
    await gui.open(page='Diagnostic guidé')
    assert not any(isinstance(e, ui.select) and 'theme_mode' not in e._markers for e in gui.user.client.elements.values())
    await gui.user.should_see('Créez ou ouvrez')


async def test_unknown_save_shows_five_indeterminate_scores(gui, store):
    project = store.create_project('Sans information')
    await gui.open(project.id, 'Diagnostic guidé')
    assert len([e for e in gui.user.find(ui.select).elements if 'theme_mode' not in e._markers]) == 15
    await gui.click(f'calms_save_{project.id}')
    assert gui.texts.count('Indéterminé') == 5
    await gui.user.should_see('0/15 réponses connues (0,0 %)')


async def test_answers_notes_and_scores_survive_new_ui_session(gui, store):
    project = store.create_project('Questionnaire conservé')
    await gui.open(project.id, 'Diagnostic guidé')
    for question, value in [('C1', 3), ('C2', 0), ('A1', 1)]:
        await gui.set(f'calms_{project.id}_{question}', value)
    await gui.set(f'calms_{project.id}_C1_note', 'Précision d’équipe')
    await gui.click(f'calms_save_{project.id}')
    await gui.user.should_see('50,0 / 100')
    await gui.user.should_see('33,3 / 100')
    await gui.user.should_see('2/3 réponses connues (66,7 %)')
    await gui.user.should_see('3/15 réponses connues (20,0 %)')
    await gui.open(project.id, 'Diagnostic guidé')
    assert gui.element(f'calms_{project.id}_C1').value == 3
    assert gui.element(f'calms_{project.id}_C2').value == 0
    assert gui.element(f'calms_{project.id}_C1_note').value == 'Précision d’équipe'
    assert store.load_calms(project.id).answers['C1'] == 3


async def test_unsaved_edits_do_not_change_saved_scores_or_database(gui, store):
    project = store.create_project('Sauvegarde explicite')
    original = store.save_calms(project.id, {'C1': 3})
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.set(f'calms_{project.id}_C1', 0)
    await gui.user.should_see('100,0 / 100')
    assert store.load_calms(project.id) == original
    await gui.click(f'calms_save_{project.id}')
    await gui.user.should_see('0,0 / 100')
    assert store.load_calms(project.id).answers['C1'] == 0
    await gui.set(f'calms_{project.id}_C1', 3)
    await gui.click(f'calms_save_{project.id}')
    await gui.user.should_see('100,0 / 100')


async def test_questionnaires_do_not_leak_between_projects_or_navigation(gui, store):
    first, second = store.create_project('A'), store.create_project('B')
    store.save_calms(first.id, {'C1': 3}, {'C1': 'Note A'})
    store.save_calms(second.id, {'C1': 0}, {'C1': 'Note B'})
    await gui.open(first.id, 'Diagnostic guidé')
    for project, answer, note in [(second, 0, 'Note B'), (first, 3, 'Note A')]:
        await gui.nav('Projets')
        await gui.click(f'open_{project.id}')
        await gui.nav('Diagnostic guidé')
        assert gui.element(f'calms_{project.id}_C1').value == answer
        assert gui.element(f'calms_{project.id}_C1_note').value == note


async def test_unsupported_saved_version_is_not_overwritten_or_disclosed(gui, store):
    project = store.create_project('Version inconnue')
    raw = json.dumps({'version': 'future', 'notes': 'FAKE_SECRET_CALMS'})
    with sqlite3.connect(store.path) as c:
        c.execute('INSERT INTO calms_drafts VALUES (?,?,?)', (project.id, 'baseline:1', raw))
    await gui.open(project.id, 'Diagnostic guidé')
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    assert not any(isinstance(e, ui.select) and 'theme_mode' not in e._markers for e in gui.user.client.elements.values())
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT draft_json FROM calms_drafts WHERE project_id=?', (project.id,)).fetchone()[0] == raw
