from devops_flow.scanner import scan_project_local


def repo(tmp_path):
    root = tmp_path / 'repository'
    for path, content in {'backend/pyproject.toml': '[project]\ndependencies=["fastapi"]',
                          'frontend/package.json': '{"dependencies":{"next":"15"}}',
                          'backend/Dockerfile': 'FROM alpine:latest',
                          'frontend/Dockerfile': 'FROM alpine:latest'}.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


async def test_current_and_saved_architecture_filter_and_action_context(gui, store, tmp_path):
    p = store.create_project('Architecture')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, repo(tmp_path))
    await gui.user.should_see('FastAPI')
    await gui.user.should_see('Next.js')
    await gui.set(f'include_calms_{p.id}', False)
    await gui.click(f'diagnostic_finalize_{p.id}')
    stored = store.latest_diagnostic(p.id)
    assert stored.snapshot.to_dict()['schema_version'] == 'diagnostic-v3'
    backend = stored.snapshot.to_dict()['architecture']['components'][0]
    await gui.set(f'findings_scope_{p.id}', backend['id'])
    assert gui.element(f'findings_scope_{p.id}').value == backend['id']
    gui.element(f'architecture_tab_{p.id}')
    fid = next(f['id'] for f in stored.snapshot.to_dict()['findings'] if f.get('scope_id') == backend['id'])
    await gui.click(f'architecture_finding_{p.id}_{fid}')
    assert gui.element(f'findings_scope_{p.id}').value == 'Tous'
    await gui.nav("Plan d'action")
    await gui.user.should_see('Périmètre : backend')
    await gui.nav('Historique / Rapports')
    await gui.user.should_see('Architecture')


async def test_manual_declaration_invalidates_scan_and_survives_restart(gui, store, tmp_path):
    root = repo(tmp_path)
    p = store.create_project('Déclaration')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.local(p.id, root)
    await gui.set(f'override_path_{p.id}', 'backend')
    await gui.set(f'override_language_{p.id}', 'Python')
    await gui.set(f'override_runtime_{p.id}', '3.12')
    await gui.click(f'override_save_{p.id}')
    assert gui.session.project().scan is None
    assert store.load_stack_overrides(p.id)[1]['backend']['runtime'] == '3.12'
    await gui.click(f'scan_run_{p.id}')
    backend = gui.session.project().scan.architecture.components[0]
    assert any(s.value == '3.12' and s.confidence == 'manual' for s in backend.effective_signals)
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.set(f'override_select_{p.id}', 'backend')
    assert gui.element(f'override_runtime_{p.id}').value == '3.12'
    await gui.click(f'override_delete_{p.id}')
    assert store.load_stack_overrides(p.id) == (2, {})


async def test_invalid_manual_form_preserves_draft_and_conflict_needs_reload(gui, store):
    p = store.create_project('Conflit de déclaration')
    await gui.open(p.id, 'Diagnostic guidé')
    await gui.set(f'override_language_{p.id}', 'Python')
    await gui.set(f'override_runtime_{p.id}', 'invalid')
    await gui.click(f'override_save_{p.id}')
    assert gui.element(f'override_language_{p.id}').value == 'Python'
    assert gui.element(f'override_runtime_{p.id}').value == 'invalid'
    await gui.set(f'override_runtime_{p.id}', '3.12')
    store.save_stack_override(p.id, '.', {'language': 'Java'}, expected_revision=0)
    await gui.click(f'override_save_{p.id}')
    await gui.user.should_see('autre session')
    assert store.load_stack_overrides(p.id)[1]['.']['language'] == 'Java'
    await gui.click(f'override_reload_{p.id}')
    await gui.set(f'override_select_{p.id}', '.')
    assert gui.element(f'override_language_{p.id}').value == 'Java'
