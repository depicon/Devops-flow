"""Déclarations Python uniquement : TOML, INI, dépendances et littéraux limités."""
import configparser
import re
from devops_flow.signals import Signal
from devops_flow.signals.parsing import (dependency_name, entry_signals, error_signal,
    runtime, text, toml_document)


class PythonScanner:
    name = 'Python'
    manifests = ('pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile')
    allowlist = manifests + ('.python-version',)

    def scan(self, files):
        results, dependencies, dependency_files = [], [], {}
        for file in self.manifests:
            if file not in files:
                continue
            results.append(Signal('language', 'Python', 'high', file, stack=self.name))
            before = len(dependencies)
            try:
                value = None
                if file == 'pyproject.toml':
                    data = toml_document(files[file])
                    project = data.get('project', {})
                    dependencies += project.get('dependencies', [])
                    dependencies += [d for group in project.get('optional-dependencies', {}).values() for d in group]
                    poetry = data.get('tool', {}).get('poetry', {}).get('dependencies', {})
                    dependencies += list(poetry)
                    value = project.get('requires-python') or poetry.get('python')
                    results.append(Signal('build_command', 'python -m build', 'medium', file, reason='convention', stack=self.name))
                elif file == 'Pipfile':
                    data = toml_document(files[file])
                    dependencies += list(data.get('packages', {})) + list(data.get('dev-packages', {}))
                    value = data.get('requires', {}).get('python_full_version') or data.get('requires', {}).get('python_version')
                elif file == 'setup.cfg':
                    parser = configparser.ConfigParser(interpolation=None)
                    parser.read_string(text(files[file]))
                    dependencies += parser.get('options', 'install_requires', fallback='').splitlines()
                    value = parser.get('options', 'python_requires', fallback=None)
                elif file == 'requirements.txt':
                    lines = text(files[file]).splitlines()
                    dependencies += [line for line in lines if not line.lstrip().startswith(('#', '-'))]
                    if any(line.lstrip().startswith(('-r', '--requirement', '-e')) for line in lines):
                        results.append(error_signal(self.name, file, 'unresolved_manifest'))
                else:
                    # Pas d'AST, d'import ou d'évaluation de setup.py. Seuls des littéraux simples.
                    content = text(files[file])
                    match = re.search(r'''python_requires\s*=\s*["']([0-9<>=!~.*, ]+)["']''', content)
                    value = match.group(1) if match else None
                    literals = re.search(r'install_requires\s*=\s*\[([^\]]{0,10000})\]', content)
                    if literals:
                        dependencies += re.findall(r'''["']([A-Za-z0-9_.-]+(?:[<>=!~][^"']*)?)["']''', literals.group(1))
                    results.append(error_signal(self.name, file, 'unresolved_manifest'))
                if runtime(value):
                    results.append(Signal('runtime', runtime(value), 'high', file, stack=self.name))
                for dependency in dependencies[before:]:
                    dependency_files.setdefault(dependency_name(dependency), file)
            except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError, configparser.Error):
                del dependencies[before:]
                results.append(error_signal(self.name, file))
        if '.python-version' in files:
            try:
                value = runtime(text(files['.python-version']).strip())
                if value:
                    results.append(Signal('runtime', value, 'high', '.python-version', stack=self.name))
            except UnicodeError:
                results.append(error_signal(self.name, '.python-version'))
        names = {dependency_name(d) for d in dependencies}
        for name, label in {'django': 'Django', 'fastapi': 'FastAPI', 'flask': 'Flask',
                            'starlette': 'Starlette', 'aiohttp': 'aiohttp'}.items():
            if name in names:
                file = dependency_files[name]
                results.append(Signal('framework', label, 'high', file, stack=self.name))
        if 'pytest' in names:
            results.append(Signal('test_command', 'pytest', 'medium', dependency_files['pytest'], reason='convention', stack=self.name))
        for package, kind in {'psycopg': 'postgres', 'psycopg2': 'postgres', 'psycopg2-binary': 'postgres',
                              'redis': 'redis', 'pymysql': 'mysql', 'mysqlclient': 'mysql', 'pika': 'rabbitmq'}.items():
            if package in names:
                results.append(Signal('service', kind, 'low', reason='declared_client_dependency', stack=self.name))
        results += entry_signals(files, self.name)
        return tuple(dict.fromkeys(results))
