"""Sept parcours critiques via le simulateur utilisateur NiceGUI officiel."""
from devops_flow.profiles import EvaluationProfile
from devops_flow.profile_io import export_markdown
from test_profiles import profile_data


async def test_profiles_list_direct_route(gui):
    await gui.user.open('/profiles')
    await gui.user.should_see('Profils d’évaluation')
    await gui.user.should_see('Référentiel de base')
    await gui.user.should_see('Aucun profil personnalisé')
    assert gui.element('profile_open_baseline')


async def test_create_minimal_profile_and_redirect(gui,store):
    await gui.user.open('/profiles/new')
    await gui.set('profile_slug','minimal')
    await gui.set('profile_name','Équipe minimale')
    for d in 'CALMS':
        await gui.set(f'profile_{d}_0_text',f'Pratique {d} ?')
    await gui.click('profile_publish')
    await gui.user.should_see('Équipe minimale')
    assert gui.user.client.sub_pages_router.current_path == '/profiles/minimal'
    profile = store.get_profile('minimal')
    assert profile and profile.version == 1
    assert all(len(d.questions)==1 for d in profile.dimensions)


async def test_import_markdown_preview_then_explicit_creation(gui,store):
    await gui.user.open('/profiles/import')
    raw = export_markdown(EvaluationProfile.from_dict(profile_data()))
    response = await gui.user.http_client.post(gui.element('profile_upload')._registered_url,
                                              files={'file':('profile.md',raw,'text/markdown')})
    assert response.status_code == 200
    await gui.user.should_see('Aperçu du profil importé')
    assert store.get_profile('equipe-data') is None
    await gui.click('profile_import_create')
    await gui.user.should_see('Équipe data')
    assert store.get_profile('equipe-data').version == 1
    assert gui.user.client.sub_pages_router.current_path == '/profiles/equipe-data'


async def test_invalid_import_clears_preview_and_disables_creation(gui,store):
    await gui.user.open('/profiles/import')
    upload = gui.element('profile_upload')
    raw = export_markdown(EvaluationProfile.from_dict(profile_data()))
    await gui.user.http_client.post(upload._registered_url, files={'file':('ok.md',raw)})
    await gui.user.should_see('Aperçu du profil importé')
    response = await gui.user.http_client.post(upload._registered_url, files={'file':('bad.md',b'\xff')})
    assert response.status_code == 200
    await gui.user.should_see('UTF-8 strict')
    assert not gui.element('profile_import_create').enabled
    response = await gui.user.http_client.post(upload._registered_url, files={'file':('large.md',b'x'*(256*1024+1))})
    assert response.status_code == 413
    assert not gui.element('profile_import_create').enabled
    assert store.get_profile('equipe-data') is None


async def test_project_creation_and_edit_assign_imposed_profile(gui,store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    await gui.open()
    await gui.click('project_new')
    await gui.set('project_name','Projet data')
    await gui.set('project_profile',profile.slug)
    await gui.click('project_create')
    project = store.list_projects()[0]
    assert store.project_profile(project.id).slug == profile.slug
    await gui.click('project_settings')
    await gui.set('project_edit_profile','baseline')
    await gui.click('project_update')
    assert store.project_profile(project.id).slug == 'baseline'
    assert store.get_project(project.id).name == 'Projet data'


async def test_diagnostic_uses_imposed_profile_without_override(gui,store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Imposé',profile_slug=profile.slug)
    await gui.open(project.id,'Diagnostic guidé')
    await gui.user.should_see('Profil utilisé pour ce diagnostic')
    assert all(getattr(e, '_props', {}).get('label') != "Profil d'évaluation imposé" for e in gui.user.client.elements.values())
    await gui.click(f'calms_save_{project.id}')
    await gui.set(f'include_calms_{project.id}',True)
    await gui.click(f'diagnostic_finalize_{project.id}')
    stored = store.latest_diagnostic(project.id)
    assert stored.snapshot.to_dict()['profile'] == profile.to_dict()


async def test_new_version_confirmation_dynamic_questions_and_pinned_project(gui,store):
    profile = store.publish_profile(EvaluationProfile.from_dict(profile_data()))
    project = store.create_project('Version figée',profile_slug=profile.slug)
    await gui.user.open('/profiles/equipe-data/edit')
    await gui.click('profile_C_add')
    await gui.set('profile_C_3_text','Question ajoutée')
    await gui.click('profile_C_3_delete')
    await gui.click('profile_C_add')
    await gui.set('profile_C_3_text','Question définitive')
    await gui.set('profile_risk_threshold',60)
    await gui.click('profile_publish')
    await gui.user.should_see('Sections modifiées : CALMS, Seuils')
    assert store.get_profile(profile.slug).version == 1
    await gui.click('profile_publish_confirm')
    await gui.user.should_see('Version 2')
    assert store.get_profile(profile.slug).version == 2
    assert len(store.get_profile(profile.slug).dimensions[0].questions)==4
    assert store.project_profile(project.id).version == 1
