"""Indices Node/TypeScript ; les valeurs des scripts ne sortent jamais du scanner."""
from devops_flow.signals import Signal
from devops_flow.signals.parsing import entry_signals, error_signal, json_document, runtime, text


class NodeScanner:
    name = 'Node.js'
    manifests = ('package.json',)
    allowlist = manifests + ('.nvmrc', '.node-version', 'tsconfig.json')

    def scan(self, files):
        result = [Signal('language', 'JavaScript', 'high', 'package.json', stack=self.name)]
        try:
            data = json_document(files['package.json'])
            dependencies = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
            if 'typescript' in dependencies or 'tsconfig.json' in files:
                result.append(Signal('language', 'TypeScript', 'high', 'package.json', stack=self.name))
            value = runtime(data.get('engines', {}).get('node'))
            if value:
                result.append(Signal('runtime', value, 'high', 'package.json', stack=self.name))
            frameworks = {'express': 'Express', '@nestjs/core': 'NestJS', 'next': 'Next.js',
                'react': 'React', 'vue': 'Vue', 'nuxt': 'Nuxt', '@angular/core': 'Angular',
                'svelte': 'Svelte', '@sveltejs/kit': 'SvelteKit'}
            for package, label in frameworks.items():
                if package in dependencies:
                    result.append(Signal('framework', label, 'high', 'package.json', stack=self.name))
            for package, kind in {'pg': 'postgres', 'redis': 'redis', 'ioredis': 'redis', 'mysql2': 'mysql', 'amqplib': 'rabbitmq'}.items():
                if package in dependencies:
                    result.append(Signal('service', kind, 'low', 'package.json', reason='declared_client_dependency', stack=self.name))
            scripts = data.get('scripts', {})
            if not isinstance(scripts, dict):
                raise ValueError
            for key, field in (('build', 'build_command'), ('test', 'test_command')):
                if isinstance(scripts.get(key), str) and scripts[key].strip():
                    result.append(Signal(field, 'npm run ' + key, 'high', 'package.json', stack=self.name))
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            result.append(error_signal(self.name, 'package.json'))
        for file in ('.nvmrc', '.node-version'):
            if file in files:
                try:
                    value = runtime(text(files[file]).strip())
                    if value:
                        result.append(Signal('runtime', value, 'high', file, stack=self.name))
                except UnicodeError:
                    result.append(error_signal(self.name, file))
        result += entry_signals(files, self.name)
        return tuple(dict.fromkeys(result))
