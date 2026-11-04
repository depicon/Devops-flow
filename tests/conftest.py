from pathlib import Path

import pytest

from devops_flow.db import ProjectStore

pytest_plugins = ['nicegui.testing.user_plugin']


def pytest_configure(config):
    config.addinivalue_line('markers', 'nicegui: interface NiceGUI simulée')


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "projects.sqlite3"


@pytest.fixture
def store(db_path: Path) -> ProjectStore:
    store = ProjectStore(db_path)
    store.initialize()
    return store


@pytest.fixture(autouse=True)
def isolated_ui_database(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setenv("DEVOPS_FLOW_DB_PATH", str(db_path))
    # Aucun test ne charge les identifiants applicatifs réels ni leur .env.
    from devops_flow.integrations import config
    monkeypatch.setattr(config, 'APP_ENV_FILE', db_path.parent / '.env')
    monkeypatch.delenv('TRELLO_API_KEY', raising=False)
    monkeypatch.delenv('TRELLO_TOKEN', raising=False)


class UiHarness:
    """Piloter les vrais éléments NiceGUI via le simulateur officiel."""
    def __init__(self, user):
        self.user = user

    @property
    def shell(self):
        return self.user.client.devops_shell

    @property
    def session(self):
        return self.shell.session

    @property
    def texts(self):
        return [str(getattr(e, 'text', getattr(e, 'content', ''))) for e in self.user.client.elements.values()]

    def element(self, key):
        return next(iter(self.user.find(marker=key).elements))

    async def idle(self):
        import asyncio
        await asyncio.sleep(.02)
        for _ in range(300):
            if not self.shell.busy:
                await asyncio.sleep(.01)
                return self
            await asyncio.sleep(.01)
        raise AssertionError('Opération UI non terminée')

    async def click(self, key):
        self.user.find(marker=key).click()
        return await self.idle()

    async def set(self, key, value):
        with self.user:
            self.element(key).value = value
        return await self.idle()

    async def nav(self, page):
        from devops_flow.ui.state import PAGES
        return await self.click('nav_' + str(PAGES.index(page)))

    async def open(self, project_id=None, page='Projets'):
        await self.user.open('/')
        if project_id:
            await self.click(f'open_{project_id}')
        if page != 'Projets':
            await self.nav(page)
        return self

    async def local(self, project_id, path):
        await self.set(f'scan_mode_{project_id}', 'Dossier local')
        await self.set(f'scan_path_{project_id}', str(path))
        return await self.click(f'scan_run_{project_id}')

    async def upload(self, project_id, data):
        # La requête HTTP exerce la route bornée ; ce helper ne prouve pas un transport navigateur.
        element = self.element(f'scan_zip_{project_id}')
        response = await self.user.http_client.post(element._registered_url, files={'file': ('test.zip', data, 'application/zip')})
        await self.idle()
        return response


@pytest.fixture
async def gui(user):
    return UiHarness(user)
