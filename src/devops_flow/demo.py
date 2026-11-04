"""Démonstration fictive embarquée ; même scanner et moteur que le parcours guidé."""

from importlib.resources import files
from io import BytesIO
import json
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from devops_flow.db import ProjectStore
from devops_flow.diagnostic import DiagnosticSnapshot, build_diagnostic
from devops_flow.scanner import scan_zip

DEMO_LIMITATION = "Démonstration fictive demo-v1 : réponses et configurations synthétiques, sans dépôt réel."
DEMO_FILES = ("Dockerfile", "compose.yaml", ".gitlab-ci.yml")


def repository_zip() -> bytes:
    """Archive déterministe en mémoire, disponible même depuis une wheel."""
    resource = files("devops_flow").joinpath("data/demo_repository")
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for name in DEMO_FILES:
            member = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(member, resource.joinpath(name).read_bytes())
    return buffer.getvalue()


def load_demo(store: ProjectStore):
    """Créer un projet neuf et son diagnostic atomiquement, sans écrasement."""
    scenario = json.loads(files("devops_flow").joinpath("data/demo_scenario.json").read_text(encoding="utf-8"))
    profile = store.get_profile('baseline', 1)
    answers = {key.lower(): value for key, value in scenario['answers'].items()}
    snapshot = build_diagnostic(answers=answers, scan=scan_zip(repository_zip()), profile=profile)
    data = snapshot.to_dict()
    data["limitations"].append(DEMO_LIMITATION)
    snapshot = DiagnosticSnapshot.from_json(json.dumps(data, ensure_ascii=False))
    return store.create_project_with_diagnostic(
        scenario["name"], scenario["description"], answers, snapshot,
    )
