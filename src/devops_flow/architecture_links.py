"""Relations locales explicites ; aucune résolution d'include ou contexte distant."""
from dataclasses import replace
import posixpath
import yaml

from devops_flow.architecture import ArchitectureRelation, ComponentDevOpsState, relative_path
from devops_flow.rules import _yaml_document, _service_type, InvalidConfiguration, UnsupportedConfiguration
from devops_flow.signals import Signal


def _reference(file, value):
    if not isinstance(value, str) or not value or value.startswith(('/', '\\')) or any(c in value for c in '$:\\'):
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(file), value))
    try:
        return relative_path(path, root=True)
    except ValueError:
        return None


def link_configurations(components, configs):
    by_path = {c.path: c for c in components}
    relations, linked, inputs, service_signals = [], {}, {}, {}
    def owner(file):
        candidates = [c for c in components if c.path == '.' or file.startswith(c.path + '/')]
        return max(candidates, key=lambda c: len(c.path) if c.path != '.' else 0, default=None)
    for file, content in sorted(configs.items()):
        name = file.rsplit('/', 1)[-1]
        if name == 'Dockerfile':
            continue
        try:
            data = _yaml_document(content)
        except (InvalidConfiguration, UnsupportedConfiguration):
            continue
        source = owner(file)
        source_id = source.id if source else 'project'
        if name == '.gitlab-ci.yml':
            includes = data.get('include', [])
            includes = includes if isinstance(includes, list) else [includes]
            for include in includes:
                path = _reference(file, include.get('local') if isinstance(include, dict) else include)
                if path not in configs or path.rsplit('/', 1)[-1] != '.gitlab-ci.yml':
                    continue
                target = owner(path)
                if target and target.id != source_id:
                    relations.append(ArchitectureRelation(source_id, target.id, 'ci_include', file))
                    linked.setdefault(target.id, set()).add(file)
            continue
        services = data.get('services')
        if not isinstance(services, dict):
            continue
        assignments = {}
        for service, configuration in services.items():
            if not isinstance(configuration, dict):
                continue
            build = configuration.get('build')
            context = build.get('context') if isinstance(build, dict) else build
            path = _reference(file, context)
            target = by_path.get(path)
            dockerfile = build.get('dockerfile', 'Dockerfile') if isinstance(build, dict) else 'Dockerfile'
            docker_path = _reference((path + '/placeholder') if path and path != '.' else 'placeholder', dockerfile) if path is not None else None
            docker_owner = owner(docker_path) if docker_path in configs else None
            if docker_owner and (target is None or (target.path == '.' and docker_owner.path != '.')):
                target = docker_owner
            if target:
                assignments[service] = target
                if target.id != source_id:
                    relations.append(ArchitectureRelation(source_id, target.id, 'compose_build', file,
                        'high' if path == target.path else 'medium'))
                    linked.setdefault(target.id, set()).add(file)
                if docker_path in configs and docker_path.rsplit('/', 1)[-1] == 'Dockerfile':
                    if docker_path not in target.devops.files:
                        linked.setdefault(target.id, set()).add(docker_path)
                    inputs.setdefault(target.id, {})[docker_path] = configs[docker_path]
        for service, target in assignments.items():
            # Seuls les services de build explicitement rattachés sont contrôlés pour ce composant.
            selected = {s: cfg for s, cfg in services.items() if assignments.get(s) == target}
            inputs.setdefault(target.id, {})[file] = yaml.safe_dump({'services': selected}).encode()
            depends = services[service].get('depends_on', [])
            dependencies = list(depends) if isinstance(depends, (list, dict)) else []
            for dep in dependencies:
                if not isinstance(dep, str):
                    continue
                other = assignments.get(dep)
                if other and other.id != target.id:
                    relations.append(ArchitectureRelation(target.id, other.id, 'compose_dependency', file))
                cfg = services.get(dep)
                kind = _service_type(cfg.get('image')) if isinstance(cfg, dict) else 'unresolved'
                if kind != 'unresolved':
                    service_signals.setdefault(target.id, []).append(Signal('service', kind, 'medium', file,
                        reason='compose_dependency'))
    output = []
    for component in components:
        extra = tuple(sorted(linked.get(component.id, ())))
        all_files = component.devops.files + extra
        missing = tuple(n for n in ('Dockerfile', '.gitlab-ci.yml') if not any(p.rsplit('/', 1)[-1] == n for p in all_files))
        output.append(replace(component, signals=component.signals + tuple(dict.fromkeys(service_signals.get(component.id, ()))),
            devops=ComponentDevOpsState(component.devops.files, extra, missing)))
    return tuple(output), tuple(dict.fromkeys(relations)), inputs
