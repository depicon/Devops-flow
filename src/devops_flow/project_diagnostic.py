"""Assemblage contextualisé ; la relecture ne consulte aucun catalogue ou dépôt."""
from copy import deepcopy
from dataclasses import asdict, fields, replace
import json
import math

from devops_flow.architecture import ProjectArchitecture, relative_path
from devops_flow.models import Finding, FINDING_STATUSES, TECHNICAL_RULE_IDS
from devops_flow.recommendations import Recommendation
from devops_flow.rules import evaluate_files
from devops_flow.scanner import ALLOWED_NAMES, ScanResult


def build_project_diagnostic(*, answers, scan, profile=None):
    from devops_flow.diagnostic import DiagnosticSnapshot, build_diagnostic
    seed = ScanResult(scan.source, (), evaluate_files({}))
    base = build_diagnostic(answers=answers, scan=seed, profile=profile).to_dict()
    findings = [dict(f, scope_id='project') for f in base['findings'] if f['category'] == 'CALMS']
    recommendations = []
    for r in base['recommendations']:
        if all(fid.startswith('F-CAL-') for fid in r['source_finding_ids']):
            rule = next(f['rule_id'] for f in findings if f['id'] == r['source_finding_ids'][0])
            recommendations.append(dict(r, scope_id='project', rule_id=rule, template_id=r['id'], template_version='recommendations-v1'))
    alignments = []
    scopes = sorted({f.scope_id for f in scan.findings})
    allowed_scopes = {'project', *(c.id for c in scan.architecture.components)}
    if not set(scopes) <= allowed_scopes:
        raise ValueError('Périmètre technique invalide.')
    for scope in scopes:
        own = {f.rule_id: f for f in scan.findings if f.scope_id == scope}
        if len(own) != sum(f.scope_id == scope for f in scan.findings):
            raise ValueError('Constats techniques dupliqués.')
        if scope != 'project' and set(own) != set(TECHNICAL_RULE_IDS):
            raise ValueError('Résultat de composant incomplet.')
        padded = tuple(own.get(f.rule_id, replace(f, status='not_applicable', explanation='Contrôle hors de ce périmètre.'))
                       for f in evaluate_files({}))
        scoped = build_diagnostic(answers=answers, scan=ScanResult(scan.source, (), padded), profile=profile).to_dict()
        mapping = {f'F-{rule}': f'F-{rule}' if scope == 'project' else f'F-{scope}-{rule}' for rule in own}
        rec_mapping = {r['id']: r['id'] if scope == 'project' else f"REC-{scope}-{next(f['rule_id'] for f in scoped['findings'] if f['id'] == r['source_finding_ids'][0])}-{r['kind'].upper()}"
                       for r in scoped['recommendations'] if any(fid in mapping for fid in r['source_finding_ids'])}
        for f in scoped['findings']:
            if f['id'] not in mapping:
                continue
            findings.append(dict(f, id=mapping[f['id']], scope_id=scope,
                                 recommendation_ids=[rec_mapping[r] for r in f['recommendation_ids']]))
        for r in scoped['recommendations']:
            if r['id'] not in rec_mapping:
                continue
            rule = next(f['rule_id'] for f in scoped['findings'] if f['id'] == r['source_finding_ids'][0])
            recommendations.append(dict(r, id=rec_mapping[r['id']], scope_id=scope, rule_id=rule,
                template_id=r['id'], template_version='recommendations-v1',
                source_finding_ids=[mapping[fid] for fid in r['source_finding_ids']]))
        for alignment in scoped['alignments']:
            if all(fid in mapping or fid.startswith('F-CAL-') for fid in alignment['source_finding_ids']):
                alignments.append(dict(alignment, id=alignment['id'] + '-' + scope,
                    source_finding_ids=[mapping.get(fid, fid) for fid in alignment['source_finding_ids']]))
    base.update(schema_version='diagnostic-v3', architecture=scan.architecture.to_dict(), findings=findings,
        recommendations=sorted(recommendations, key=lambda r: (-r['priority'], r['id'])), alignments=alignments)
    base['versions'].update(engine='diagnostic-engine-v3', scanner=scan.scanner_version,
                            rules=scan.rules_version, recommendations='recommendations-v2')
    base['inputs']['scan'] = {'source': scan.source, 'files': list(scan.files), 'override_revision': scan.override_revision}
    base['limitations'].append('Architecture DevOps issue de déclarations et indices ; les fichiers non observés ne prouvent pas une absence de pratique.')
    raw = json.dumps(base, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return DiagnosticSnapshot.from_json(raw)


def validate_project_snapshot(data):
    from devops_flow.diagnostic import _validate_snapshot
    architecture = ProjectArchitecture.from_dict(data['architecture'])
    scopes = {'project', *(c.id for c in architecture.components)}
    scan = data['inputs']['scan']
    if (not isinstance(scan, dict) or set(scan) != {'source', 'files', 'override_revision'}
            or scan['source'] not in {'local', 'zip'} or type(scan['override_revision']) is not int
            or scan['override_revision'] < 0 or not isinstance(scan['files'], list)
            or len(scan['files']) > 1000 or len(set(scan['files'])) != len(scan['files'])):
        raise ValueError
    for path in scan['files']:
        relative_path(path)
    from devops_flow.architecture import is_manifest
    # Noms du contrat diagnostic-v3, indépendants de l'évolution du registre courant.
    index_names = {'Dockerfile', 'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml',
        '.gitlab-ci.yml', '.python-version', '.nvmrc', '.node-version', 'tsconfig.json', 'rust-toolchain',
        'rust-toolchain.toml', 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile',
        'package.json', 'go.mod', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'Cargo.toml'}
    entry_files = {s.file for c in architecture.components for s in c.signals if s.field == 'entrypoint' and s.file}
    if any(not (p.rsplit('/', 1)[-1] in index_names or is_manifest(p) or p in entry_files) for p in scan['files']):
        raise ValueError
    if any(path not in scan['files'] for c in architecture.components for path in (*c.manifests, *c.devops.files, *c.devops.linked_files)):
        raise ValueError
    findings = data['findings']
    if not isinstance(findings, list) or len(findings) > 5 + 21 * 6:
        raise ValueError
    ids = set()
    for f in findings:
        if (set(f) != {field.name for field in fields(Finding)} | {'scope_id'}
                or f['scope_id'] not in scopes or f['status'] not in FINDING_STATUSES
                or f['source'] not in {'déclaré', 'observé', 'inconnu'}
                or f['severity'] not in {None, 'élevée', 'modérée'}):
            raise ValueError
        expected = f"F-{f['rule_id']}" if f['rule_id'].startswith('CAL-') or f['scope_id'] == 'project' else f"F-{f['scope_id']}-{f['rule_id']}"
        if f['id'] != expected or f['id'] in ids:
            raise ValueError
        ids.add(f['id'])
        if f['rule_id'].startswith('CAL-') and f['scope_id'] != 'project':
            raise ValueError
        if not all(isinstance(f[k], str) for k in ('category', 'explanation', 'limitation')):
            raise ValueError
        for e in f['evidence']:
            if (set(e) != {'code', 'question_id', 'value', 'file', 'line'} or not isinstance(e['code'], str)
                    or (e['line'] is not None and (type(e['line']) is not int or e['line'] < 1))):
                raise ValueError
            if e['file'] is not None:
                relative_path(e['file'])
                if e['file'] not in scan['files'] or e['file'].rsplit('/', 1)[-1] not in ALLOWED_NAMES:
                    raise ValueError
            if e['value'] is not None and e['value'] != 'unknown' and not (type(e['value']) is int and 0 <= e['value'] <= 3):
                raise ValueError
    by_id = {f['id']: f for f in findings}
    rec_ids = set()
    for r in data['recommendations']:
        if (set(r) != {field.name for field in fields(Recommendation)} | {'scope_id', 'rule_id', 'template_id', 'template_version'}
                or r['scope_id'] not in scopes or r['kind'] not in {'risk', 'verify'}
                or not r['source_finding_ids'] or r['id'] in rec_ids
                or type(r['impact']) is not int or not 1 <= r['impact'] <= 3
                or type(r['effort']) is not int or not 1 <= r['effort'] <= 3
                or type(r['priority']) not in (int, float) or not math.isfinite(r['priority'])
                or r['priority'] != r['impact'] / r['effort']
                or r['severity'] not in {None, 'élevée', 'modérée'}
                or not all(isinstance(r[k], str) for k in ('title', 'description', 'justification'))):
            raise ValueError
        if r['template_id'] != f"REC-{r['rule_id']}-{r['kind'].upper()}" or r['template_version'] != 'recommendations-v1':
            raise ValueError
        expected = r['template_id'] if r['rule_id'].startswith('CAL-') or r['scope_id'] == 'project' else f"REC-{r['scope_id']}-{r['rule_id']}-{r['kind'].upper()}"
        if r['id'] != expected or any(fid not in ids or by_id[fid]['scope_id'] != r['scope_id'] or by_id[fid]['rule_id'] != r['rule_id'] for fid in r['source_finding_ids']):
            raise ValueError
        rec_ids.add(r['id'])
    if any(not set(f['recommendation_ids']) <= rec_ids for f in findings):
        raise ValueError
    # Réutiliser le validateur historique pour CALMS/profil/scores et leurs références,
    # sans appeler le moteur, le registre ou un catalogue lors de la lecture.
    legacy = deepcopy(data)
    legacy.pop('architecture')
    legacy['schema_version'] = 'diagnostic-v2' if data.get('profile') else 'diagnostic-v1'
    legacy['inputs']['scan'] = {k: scan[k] for k in ('source', 'files')}
    selected = {}
    for f in findings:
        selected.setdefault(f['rule_id'], f)
    legacy['findings'] = [dict({k: v for k, v in f.items() if k != 'scope_id'},
        id='F-' + f['rule_id'], recommendation_ids=[]) for f in selected.values()]
    legacy['recommendations'] = []
    legacy['alignments'] = [dict(a, source_finding_ids=['F-' + by_id[fid]['rule_id'] for fid in a['source_finding_ids']])
                             for a in data['alignments']]
    _validate_snapshot(legacy)
    enabled = {f['rule_id'] for f in legacy['findings'] if f['rule_id'] in TECHNICAL_RULE_IDS}
    for component in architecture.components:
        if {f['rule_id'] for f in findings if f['scope_id'] == component.id} != enabled:
            raise ValueError
