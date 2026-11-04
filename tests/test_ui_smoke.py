import pytest
from devops_flow.db import ProjectStore
from devops_flow.ui.state import PAGES


@pytest.mark.parametrize('page', PAGES)
async def test_each_page_is_accessible(gui, page):
    await gui.open(page=page)
    await gui.user.should_see(page)
    if page != 'Projets':
        await gui.user.should_see('Créez ou ouvrez')


async def test_create_open_and_reopen_project_in_new_ui_session(gui, db_path):
    await gui.open()
    await gui.click('project_new')
    await gui.set('project_name', 'Projet Démonstration')
    await gui.set('project_description', 'Contexte conservé après relance')
    await gui.click('project_create')
    project = ProjectStore(db_path).list_projects()[0]
    assert gui.session.active_project_id == project.id
    await gui.open(project.id, 'Diagnostic guidé')
    await gui.user.should_see(project.name)
    assert gui.session.store.get_project(project.id).description == project.description


async def test_blank_name_shows_french_error_and_creates_nothing(gui, db_path):
    await gui.open()
    await gui.click('project_new')
    await gui.set('project_name', '   ')
    await gui.click('project_create')
    await gui.user.should_see('Le nom du projet est obligatoire.')
    assert ProjectStore(db_path).list_projects() == []


async def test_selecting_a_different_project_replaces_the_displayed_context(gui, store):
    first, second = store.create_project('Projet A', 'Contexte A'), store.create_project('Projet B', 'Contexte B')
    await gui.open(first.id)
    await gui.user.should_see('Contexte A')
    await gui.click(f'open_{second.id}')
    await gui.user.should_see('Contexte B')
    await gui.user.should_not_see('Contexte A')


async def test_storage_error_is_neutral(gui, db_path):
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b'invalid-db-with-FAKE_SECRET_P1')
    await gui.user.open('/')
    await gui.user.should_see('Le stockage local est inaccessible')
    assert 'FAKE_SECRET' not in ' '.join(gui.texts)
    assert str(db_path) not in ' '.join(gui.texts)
