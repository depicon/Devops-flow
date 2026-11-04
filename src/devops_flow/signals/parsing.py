"""Parseurs bornés et valeurs contrôlées ; jamais d'interpolation ou de code exécuté."""
import json
import re
import tomllib

from devops_flow.signals import Signal


def text(content):
    return content.decode('utf-8')


def json_document(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    value = json.loads(text(content), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError
    return value


def toml_document(content):
    return tomllib.loads(text(content))


def runtime(value):
    if isinstance(value, str) and len(value) <= 80 and re.fullmatch(r'[vV0-9xX*~^<>=!|., +\-]+', value) and re.search(r'\d', value):
        return value.strip()
    return None


def dependency_name(value):
    if not isinstance(value, str):
        return ''
    match = re.match(r'^\s*([A-Za-z0-9_.-]+)(?:\[.*?\])?(?:\s|[<>=!~;@]|$)', value)
    return match.group(1).lower().replace('_', '-') if match else ''


def error_signal(stack, file, reason='invalid_manifest'):
    return Signal('runtime', None, 'unknown', file, reason=reason, stack=stack)


def entry_signals(files, stack):
    result = []
    extensions = {'Python': ('.py',), 'Node.js': ('.js', '.mjs', '.cjs', '.ts', '.tsx'),
                  'Go': ('.go',), 'Java': ('.java',), 'Rust': ('.rs',)}[stack]
    for path, content in sorted(files.items()):
        if not path.endswith(extensions) or path == 'setup.py':
            continue
        try:
            lines = text(content).splitlines()[:200]
        except UnicodeError:
            continue
        result.append(Signal('entrypoint', path, 'low', path, reason='candidate', stack=stack))
        for n, line in enumerate(lines, 1):
            # Indices lexicaux seulement ; une occurrence dans une chaîne peut être trompeuse.
            if line.lstrip().startswith(('#', '//', '*')):
                continue
            for name in re.findall(r'''(?:(?:getenv|Getenv|LookupEnv)\(\s*|environ(?:\.get)?[\[(]\s*|env(?:\.get)?[\[(]\s*|env::var\(\s*)["']([A-Z_][A-Z0-9_]{0,127})["']''', line):
                result.append(Signal('environment', name, 'low', path, n, 'lexical_candidate', stack))
            for name in re.findall(r'process\.env\.([A-Z_][A-Z0-9_]{0,127})', line):
                result.append(Signal('environment', name, 'low', path, n, 'lexical_candidate', stack))
            matches = re.findall(r'(?:\bport\s*[:=]\s*|\.listen\(\s*|ListenAndServe\(\s*["\']:|(?:::|\.)bind\(\s*["\'](?:127\.0\.0\.1|0\.0\.0\.0):)(\d{1,5})\b', line, re.I)
            matches += re.findall(r'''(?:::|\.)bind\(\s*\(\s*["'](?:127\.0\.0\.1|0\.0\.0\.0)["']\s*,\s*(\d{1,5})\s*\)''', line)
            for port in matches:
                if 1 <= int(port) <= 65535:
                    result.append(Signal('port', int(port), 'low', path, n, 'lexical_candidate', stack))
    return tuple(dict.fromkeys(result))
