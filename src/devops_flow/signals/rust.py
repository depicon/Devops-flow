"""Déclarations Cargo et toolchain ; aucune exécution de build.rs ou macro."""
from devops_flow.signals import Signal
from devops_flow.signals.parsing import entry_signals, error_signal, runtime, text, toml_document


class RustScanner:
    name = 'Rust'
    manifests = ('Cargo.toml',)
    allowlist = manifests + ('rust-toolchain', 'rust-toolchain.toml')

    def scan(self, files):
        result = [Signal('language', 'Rust', 'high', 'Cargo.toml', stack=self.name)]
        try:
            data = toml_document(files['Cargo.toml'])
            value = runtime(data.get('package', {}).get('rust-version'))
            if value:
                result.append(Signal('runtime', value, 'high', 'Cargo.toml', stack=self.name))
            dependencies = {**data.get('dependencies', {}), **data.get('dev-dependencies', {})}
            for dependency, label in {'axum': 'Axum', 'actix-web': 'Actix Web', 'rocket': 'Rocket'}.items():
                if dependency in dependencies:
                    result.append(Signal('framework', label, 'high', 'Cargo.toml', stack=self.name))
            for package, kind in {'tokio-postgres': 'postgres', 'postgres': 'postgres', 'redis': 'redis',
                                  'mysql': 'mysql', 'mysql_async': 'mysql', 'lapin': 'rabbitmq'}.items():
                if package in dependencies:
                    result.append(Signal('service', kind, 'low', 'Cargo.toml', reason='declared_client_dependency', stack=self.name))
            result += [Signal(field, command, 'medium', 'Cargo.toml', reason='convention', stack=self.name)
                       for field, command in (('build_command', 'cargo build'), ('test_command', 'cargo test'))]
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            result.append(error_signal(self.name, 'Cargo.toml'))
        for file in ('rust-toolchain', 'rust-toolchain.toml'):
            if file in files:
                try:
                    value = (toml_document(files[file]).get('toolchain', {}).get('channel')
                             if file.endswith('.toml') else text(files[file]).strip())
                    if runtime(value):
                        result.append(Signal('runtime', runtime(value), 'high', file, stack=self.name))
                except (ValueError, UnicodeError, AttributeError):
                    result.append(error_signal(self.name, file))
        return tuple(dict.fromkeys(result)) + entry_signals(files, self.name)
