"""Tests SQLite exécutables avec unittest, et collectables aussi par pytest."""

from datetime import timedelta
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from devops_assist.db import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="devops-assist-db-")
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "data" / "projects.sqlite3"
        self.store = ProjectStore(self.db_path)
        self.store.initialize()

    def test_initialization_creates_parent_and_empty_database(self):
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(self.store.list_projects(), [])

    def test_project_survives_reinitialization_and_new_store(self):
        project = self.store.create_project("  Projet Équipe  ", "  Contexte d'essai  ")
        reopened = ProjectStore(self.db_path)
        reopened.initialize()
        reopened.initialize()
        self.assertEqual(reopened.get_project(project.id), project)
        self.assertEqual(reopened.list_projects(), [project])
        self.assertEqual(project.name, "Projet Équipe")
        self.assertEqual(project.description, "Contexte d'essai")
        self.assertEqual(project.created_at.utcoffset(), timedelta(0))
        self.assertEqual(project.updated_at, project.created_at)

    def test_projects_are_distinct_and_lookups_do_not_cross_data(self):
        first = self.store.create_project("Même nom", "Contexte A")
        second = self.store.create_project("Même nom", "Contexte B")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self.store.get_project(first.id).description, "Contexte A")
        self.assertEqual(self.store.get_project(second.id).description, "Contexte B")
        self.assertIsNone(self.store.get_project("inconnu"))
        self.assertEqual({p.id for p in self.store.list_projects()}, {first.id, second.id})

    def test_invalid_input_creates_no_project(self):
        invalid = [("", ""), (" \n\t", ""), ("a" * 121, ""),
                   ("Projet", "a" * 2001), (None, ""), ("Projet", None)]
        for name, description in invalid:
            with self.subTest(name=name, description_type=type(description).__name__):
                with self.assertRaises(ValueError):
                    self.store.create_project(name, description)
        self.assertEqual(self.store.list_projects(), [])

    def test_maximum_lengths_and_optional_description(self):
        project = self.store.create_project("a" * 120, "é" * 2000)
        self.assertEqual(self.store.get_project(project.id), project)
        self.assertEqual(self.store.create_project("Sans description").description, "")

    def test_queries_treat_input_as_data(self):
        name = "Projet'); DROP TABLE projects; --"
        project = self.store.create_project(name)
        self.assertEqual(self.store.get_project(project.id).name, name)
        self.assertIsNone(self.store.get_project("' OR 1=1 --"))
        self.assertEqual(len(self.store.list_projects()), 1)

    def test_projects_are_listed_newest_first(self):
        self.store.create_project("Ancien")
        self.store.create_project("Récent")
        self.assertEqual([p.name for p in self.store.list_projects()], ["Récent", "Ancien"])

    def test_invalid_database_fails_without_replacing_it(self):
        path = Path(self.directory.name) / "invalid.sqlite3"
        initial = b"not a SQLite database"
        path.write_bytes(initial)
        with self.assertRaises(sqlite3.DatabaseError):
            ProjectStore(path).initialize()
        self.assertEqual(path.read_bytes(), initial)

    def test_existing_p1_database_migrates_without_losing_project(self):
        legacy_path = Path(self.directory.name) / "legacy.sqlite3"
        project = self.store.create_project("Projet avant CALMS", "Contexte préservé")
        with sqlite3.connect(legacy_path) as connection:
            connection.execute("""CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", (
                project.id, project.name, project.description,
                project.created_at.isoformat(), project.updated_at.isoformat(),
            ))
        migrated = ProjectStore(legacy_path)
        migrated.initialize()
        migrated.initialize()
        self.assertEqual(migrated.get_project(project.id), project)
        self.assertIsNone(migrated.load_calms(project.id))
        saved = migrated.save_calms(project.id, {"C1": 3})
        self.assertEqual(migrated.load_calms(project.id), saved)


if __name__ == "__main__":
    unittest.main()
