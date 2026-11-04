"""Maven XML borné et littéraux Gradle ; aucune évaluation de build."""
import re
import xml.etree.ElementTree as ET
from devops_flow.signals import Signal
from devops_flow.signals.parsing import entry_signals, error_signal, runtime, text


def _pom(content):
    source = text(content)
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', source, re.I):
        raise ValueError
    root = ET.fromstring(source)
    pending, nodes = [(root, 1)], 0
    result = {}
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 10000:
            raise ValueError
        result.setdefault(node.tag.rsplit('}', 1)[-1], []).append(node.text or '')
        pending.extend((child, depth + 1) for child in node)
    if root.tag.rsplit('}', 1)[-1] != 'project':
        raise ValueError
    return result


class JavaScanner:
    name = 'Java'
    manifests = ('pom.xml', 'build.gradle', 'build.gradle.kts')
    allowlist = manifests

    def scan(self, files):
        result = []
        for file in self.manifests:
            if file not in files:
                continue
            result.append(Signal('language', 'Java', 'high', file, stack=self.name))
            try:
                if file == 'pom.xml':
                    data = _pom(files[file])
                    versions = [v for k in ('java.version', 'maven.compiler.release', 'maven.compiler.source') for v in data.get(k, [])]
                    artifacts = data.get('artifactId', [])
                    frameworks = [('Spring Boot', any(v.startswith('spring-boot') for v in artifacts)),
                                  ('Quarkus', any(v.startswith('quarkus-') for v in artifacts)),
                                  ('Micronaut', any(v.startswith('micronaut-') for v in artifacts))]
                    clients = set(artifacts)
                    commands = ('mvn package', 'mvn test')
                else:
                    content = re.sub(r'/\*.*?\*/|//[^\n]*', '', text(files[file]), flags=re.S)
                    versions = re.findall(r'(?:JavaLanguageVersion\.of\(|jvmToolchain\(|sourceCompatibility\s*=\s*["\']?)(\d+(?:\.\d+)?)', content)
                    frameworks = [(label, bool(re.search(pattern, content))) for label, pattern in (
                        ('Spring Boot', r'["\']org\.springframework\.boot(?::|["\'])'),
                        ('Quarkus', r'["\']io\.quarkus(?::|["\'])'),
                        ('Micronaut', r'["\']io\.micronaut(?:\.|:)'))]
                    clients = set(re.findall(r'''["'][A-Za-z0-9_.-]+:([A-Za-z0-9_.-]+):''', content))
                    commands = ('gradle build', 'gradle test')
                    if '${' in content:
                        result.append(error_signal(self.name, file, 'unresolved_manifest'))
                for version in versions:
                    if runtime(version):
                        result.append(Signal('runtime', runtime(version), 'high', file, stack=self.name))
                for label, found in frameworks:
                    if found:
                        result.append(Signal('framework', label, 'high', file, stack=self.name))
                for package, kind in {'postgresql': 'postgres', 'jedis': 'redis', 'lettuce-core': 'redis',
                                      'mysql-connector-j': 'mysql', 'mysql-connector-java': 'mysql', 'amqp-client': 'rabbitmq'}.items():
                    if package in clients:
                        result.append(Signal('service', kind, 'low', file, reason='declared_client_dependency', stack=self.name))
                result += [Signal(field, command, 'medium', file, reason='convention', stack=self.name)
                           for field, command in zip(('build_command', 'test_command'), commands)]
            except (ET.ParseError, ValueError, UnicodeError, RecursionError):
                result.append(error_signal(self.name, file))
        return tuple(dict.fromkeys(result)) + entry_signals(files, self.name)
