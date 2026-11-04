"""État temporaire d'un client ; le stockage métier reste dans ProjectStore."""

import asyncio
from dataclasses import dataclass, field
import hashlib
from uuid import uuid4

from nicegui import run

from devops_flow.calms import load_questionnaire, UNKNOWN
from devops_flow.diagnostic import build_diagnostic
from devops_flow.reports import build_export
from devops_flow.scanner import (DEFAULT_LIMITS, ScanError, ScanResult,
    scan_project_local as scan_local, scan_project_zip as scan_zip)
from devops_flow.remote import analyze_remote, parse_repository

PAGES = ("Projets", "Diagnostic guidé", "Plan d'action", "Historique / Rapports")


@dataclass
class ProjectState:
    mode: str = "Archive ZIP"
    path: str = ""
    revision: int = 0
    scan: ScanResult | None = None
    pending_zip: bytes | None = None
    answers: dict | None = None
    notes: dict = field(default_factory=dict)
    include_calms: bool | None = None
    include_scan: bool | None = None
    submission_key: str = field(default_factory=lambda: str(uuid4()))
    fingerprint: str | None = None
    submitted: bool = False
    selected: dict = field(default_factory=dict)
    focused_finding: str | None = None
    reports: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    profile_ref: str | None = None
    draft_saved_at: str = ''
    remote_provider: str = 'gitlab'
    remote_url: str = ''
    remote_ref: str = ''
    remote_token: str = field(default='', repr=False)
    remote_loaded: bool = False
    acquisition: object | None = None
    acquisition_id: str | None = None
    documentation: dict = field(default_factory=dict)
    trello: dict = field(default_factory=dict)
    diagnostic_step: str = 'prepare'
    diagnostic_sources: str = 'both'
    override_editor: str = '__new__'
    override_drafts: dict = field(default_factory=dict)

    def questionnaire_dirty(self, saved):
        if self.answers is None:
            return False
        previous = saved.answers if saved else {}
        answers_changed = any(value != previous.get(key, UNKNOWN) for key, value in self.answers.items())
        notes = {key: value.strip() for key, value in self.notes.items() if value.strip()}
        return answers_changed or notes != (saved.notes if saved else {})

    def invalidate(self) -> None:
        self.revision += 1
        self.scan = None
        self.pending_zip = None
        self.acquisition = None
        self.acquisition_id = None

    def new_submission(self) -> None:
        self.submission_key = str(uuid4())
        self.fingerprint = None
        self.submitted = False


@dataclass
class Session:
    store: object
    active_project_id: str | None = None
    page: str = "Projets"
    projects: dict[str, ProjectState] = field(default_factory=dict)

    def project(self, project_id: str | None = None) -> ProjectState:
        key = project_id or self.active_project_id
        if key is None:
            raise ValueError("Ouvrez un projet avant de continuer.")
        return self.projects.setdefault(key, ProjectState())

    def navigate(self, page: str, diagnostic_id: str | None = None, finding_id: str | None = None) -> None:
        if self.active_project_id and self.page == "Diagnostic guidé" and page != self.page:
            self.project().pending_zip = None
        self.page = page
        if diagnostic_id:
            self.project().selected[page] = diagnostic_id
        if self.active_project_id:
            self.project().focused_finding = finding_id

    def open_project(self, project_id: str) -> None:
        if self.active_project_id:
            self.project().pending_zip = None
        self.active_project_id = project_id

    def load_remote(self, project_id):
        state = self.project(project_id)
        if not state.remote_loaded:
            saved = self.store.remote_repository(project_id)
            if saved:
                state.remote_provider, state.remote_ref = saved.provider, saved.ref
                state.remote_url = 'https://' + saved.host + '/' + saved.project_path
            state.remote_loaded = True
        return state

    def load_form(self, project_id: str) -> ProjectState:
        state = self.project(project_id)
        profile = self.store.project_profile(project_id)
        ref = f'{profile.slug}:{profile.version}'
        if state.profile_ref != ref:
            state.answers = None
            state.notes = {}
            state.include_calms = None
            state.new_submission()
            state.profile_ref = ref
        saved = self.store.load_calms(project_id, profile=profile)
        if state.answers is None:
            state.answers = {q: (saved.answers.get(q, UNKNOWN) if saved else UNKNOWN)
                             for q in profile.question_ids}
            state.notes = dict(saved.notes) if saved else {}
            state.draft_saved_at = saved.saved_at.isoformat() if saved else ''
        return state

    async def save_calms(self, project_id: str):
        state = self.project(project_id)
        async with state.lock:
            slug, version = state.profile_ref.rsplit(':', 1)
            profile = self.store.get_profile(slug, int(version))
            saved = await run.io_bound(self.store.save_calms, project_id, dict(state.answers), dict(state.notes),
                                      profile=profile, expected_saved_at=state.draft_saved_at)
            state.draft_saved_at = saved.saved_at.isoformat()
            return saved

    def change_source(self, project_id: str, *, mode=None, path=None) -> None:
        state = self.project(project_id)
        if mode is not None:
            state.mode = mode
        if path is not None:
            state.path = path
        state.invalidate()

    def accept_zip(self, project_id: str, data: bytes, revision: int) -> bool:
        state = self.project(project_id)
        if state.mode != "Archive ZIP" or state.revision != revision:
            return False
        if len(data) > DEFAULT_LIMITS.archive_bytes:
            state.invalidate()
            raise ScanError("archive_budget")
        state.pending_zip = data
        return True

    async def analyze(self, project_id: str) -> bool:
        state = self.project(project_id)
        state.scan = None
        state.acquisition = None
        state.acquisition_id = None
        state.revision += 1
        revision, mode, path, payload = state.revision, state.mode, state.path, state.pending_zip
        state.pending_zip = None
        if mode == "Archive ZIP" and payload is None:
            raise ValueError("Choisissez une archive ZIP avant l'analyse.")
        if mode == "Dossier local" and not path.strip():
            raise ValueError("Renseignez le chemin du dossier avant l'analyse.")
        acquisition = None
        if mode == 'Dépôt distant':
            reference = parse_repository(state.remote_provider, state.remote_url, state.remote_ref)
            acquisition = await run.io_bound(analyze_remote, reference, state.remote_token)
            result = acquisition.scan
        else:
            override_revision, overrides = await run.io_bound(self.store.load_stack_overrides, project_id)
            options = {'overrides': overrides, 'override_revision': override_revision} if override_revision else {}
            result = await run.io_bound(scan_zip, payload, **options) if mode == "Archive ZIP" else await run.io_bound(scan_local, path, **options)
            current_revision, _ = await run.io_bound(self.store.load_stack_overrides, project_id)
            if current_revision != override_revision:
                raise ValueError('Les déclarations ont changé pendant l’analyse. Relancez le scan.')
        if state.revision != revision:
            return False
        if acquisition:
            acquisition_id = await run.io_bound(self.store.save_remote_acquisition, project_id, acquisition)
            if state.revision != revision:
                return False
            state.acquisition_id = acquisition_id
            state.acquisition = acquisition
        state.scan = result
        return True

    async def finalize(self, project_id: str):
        state = self.project(project_id)
        async with state.lock:
            use_calms, use_scan, revision = state.include_calms, state.include_scan, state.revision
            profile = await run.io_bound(self.store.project_profile, project_id)
            if state.profile_ref is not None and state.profile_ref != f'{profile.slug}:{profile.version}':
                raise ValueError('Le profil du projet a changé. Rechargez le questionnaire.')
            scan = state.scan if use_scan else None
            saved = await run.io_bound(self.store.load_calms, project_id, profile=profile) if use_calms else None
            if state.revision != revision:
                raise ValueError('Les sources ont changé pendant la finalisation. Vérifiez votre sélection et recommencez.')
            if (use_calms and saved is None) or (use_scan and scan is None):
                raise ValueError("Une source sélectionnée est indisponible. Enregistrez le questionnaire, relancez le scan ou décochez cette source.")
            snapshot = build_diagnostic(answers=saved.answers if saved else None, scan=scan, profile=profile)
            fingerprint = hashlib.sha256(snapshot.to_json().encode()).hexdigest()
            if use_scan and state.acquisition_id:
                fingerprint = hashlib.sha256((fingerprint + state.acquisition_id).encode()).hexdigest()
            if state.fingerprint is not None and state.fingerprint != fingerprint:
                state.new_submission()
            state.fingerprint = fingerprint
            stored = await run.io_bound(self.store.save_diagnostic, project_id, state.submission_key, snapshot,
                                       expected_draft=saved.saved_at.isoformat() if saved else None,
                                       remote_acquisition_id=state.acquisition_id if use_scan else None)
            state.submitted = True
            state.selected['Diagnostic guidé'] = stored.id
            state.focused_finding = None
            return stored

    def report(self, project_id: str, diagnostic_id: str, *, refresh=False):
        diagnostic, actions = self.store.diagnostic_state(project_id, diagnostic_id)
        state = [(a.id, a.recommendation_id, a.position, a.status, a.created_at.isoformat(), a.updated_at.isoformat()) for a in actions]
        fingerprint = hashlib.sha256((diagnostic.snapshot.to_json() + repr(state)).encode()).hexdigest()
        cache = self.project(project_id).reports
        if refresh or diagnostic_id not in cache or cache[diagnostic_id][0] != fingerprint:
            cache[diagnostic_id] = (fingerprint, build_export(diagnostic, actions))
        return cache[diagnostic_id][1]

    def dispose(self) -> None:
        self.projects.clear()
