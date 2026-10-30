from pathlib import Path

import pytest

from devops_assist.db import ProjectStore


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
    monkeypatch.setenv("DEVOPS_ASSIST_DB_PATH", str(db_path))
