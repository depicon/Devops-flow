"""Candidats autorisés ; aucun chemin arbitraire ou résolution de lien."""
import re
from devops_flow.architecture import relative_path
from devops_flow.signals.parsing import json_document, toml_document


def declared_candidates(files):
    candidates = []
    def add(value, extensions):
        if isinstance(value, str) and value.endswith(extensions):
            candidates.append(value)
    try:
        if 'package.json' in files:
            data = json_document(files['package.json'])
            add(data.get('main'), ('.js', '.mjs', '.cjs', '.ts', '.tsx'))
            bins = data.get('bin', {})
            for value in list(bins.values()) if isinstance(bins, dict) else [bins]:
                add(value, ('.js', '.mjs', '.cjs', '.ts', '.tsx'))
            start = data.get('scripts', {}).get('start', '')
            match = re.fullmatch(r'(?:node|tsx|ts-node) ([A-Za-z0-9_./-]+)', start) if isinstance(start, str) else None
            if match:
                add(match.group(1), ('.js', '.mjs', '.cjs', '.ts', '.tsx'))
        if 'pyproject.toml' in files:
            scripts = toml_document(files['pyproject.toml']).get('project', {}).get('scripts', {})
            for value in scripts.values():
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', value):
                    candidates.append(value.split(':')[0].replace('.', '/') + '.py')
        if 'Cargo.toml' in files:
            for binary in toml_document(files['Cargo.toml']).get('bin', []):
                add(binary.get('path'), ('.rs',))
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        pass
    result = []
    for path in candidates:
        try:
            if isinstance(path, str) and path.endswith(('.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.rs')):
                result.append(relative_path(path.removeprefix('./')))
        except ValueError:
            continue
    return tuple(sorted(set(result)))
