import json

import pytest

from devops_flow.signals.registry import default_registry
from devops_flow.scanner import scan_project_zip
from tests.test_project_scanner import zipped


@pytest.mark.parametrize('file,content,language,framework,version', [
    ('pyproject.toml', '[project]\nrequires-python=">=3.11"\ndependencies=["fastapi>=0.1", "pytest"]', 'Python', 'FastAPI', '>=3.11'),
    ('package.json', '{"engines":{"node":">=20"},"dependencies":{"next":"15","typescript":"5"},"scripts":{"build":"SECRET=value cmd"}}', 'TypeScript', 'Next.js', '>=20'),
    ('go.mod', 'module example.org/api\ngo 1.23\nrequire github.com/gin-gonic/gin v1.0.0', 'Go', 'Gin', '1.23'),
    ('pom.xml', '<project><properties><java.version>21</java.version></properties><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>', 'Java', 'Spring Boot', '21'),
    ('build.gradle.kts', 'plugins { id("io.quarkus") }\njava { toolchain { languageVersion = JavaLanguageVersion.of(21) }}', 'Java', 'Quarkus', '21'),
    ('Cargo.toml', '[package]\nname="api"\nrust-version="1.80"\n[dependencies]\naxum="0.7"', 'Rust', 'Axum', '1.80')])
def test_five_scanners_controlled_declarations(file, content, language, framework, version):
    signals = default_registry().scan({file: content.encode()})
    assert any(s.field == 'language' and s.value == language for s in signals)
    assert any(s.field == 'framework' and s.value == framework for s in signals)
    assert any(s.field == 'runtime' and s.value == version for s in signals)
    assert 'SECRET=value' not in repr(signals)


@pytest.mark.parametrize('file,content', [('pyproject.toml', '['), ('package.json', '{'),
    ('go.mod', 'not a module'), ('pom.xml', '<!DOCTYPE x [<!ENTITY y "SECRET">]><project/>'),
    ('build.gradle', b'\xff'), ('Cargo.toml', '[')])
def test_invalid_manifests_unknown_not_fabricated(file, content):
    signals = default_registry().scan({file: content.encode() if isinstance(content, str) else content})
    assert any(s.reason == 'invalid_manifest' for s in signals)
    assert not any(s.field == 'framework' for s in signals)


def test_entrypoint_candidates_environment_port_and_secret_exclusions():
    result = scan_project_zip(zipped({'package.json': '{"main":"lib/launch.js"}',
        'lib/launch.js': 'app.listen(8088); const token=process.env.API_TOKEN; const x="SECRET_VALUE";',
        'lib/ignored.js': 'app.listen(9999);', '.env': 'API_TOKEN=SECRET_ENV'}))
    signals = result.architecture.components[0].signals
    assert any(s.field == 'port' and s.value == 8088 and s.confidence == 'low' for s in signals)
    assert any(s.field == 'environment' and s.value == 'API_TOKEN' for s in signals)
    assert not any(s.value == 9999 for s in signals)
    assert 'SECRET_' not in repr(result)


def test_first_200_lines_candidates_limit_and_cross_component_paths():
    files = {'package.json': '{"main":"backend/private.js"}', 'backend/package.json': '{}',
             'backend/private.js': 'app.listen(9999)', 'main.js': '\n' * 200 + 'app.listen(8888)'}
    result = scan_project_zip(zipped(files))
    assert not any(s.value in (9999, 8888) for c in result.architecture.components for s in c.signals)


@pytest.mark.parametrize('main', ['../secret.js', '/tmp/secret.js', '.git/main.js', 'secrets/main.js'])
def test_declared_entrypoint_cannot_escape(main):
    result = scan_project_zip(zipped({'package.json': json.dumps({'main': main})}))
    assert not any(s.field == 'entrypoint' for s in result.architecture.components[0].signals)


def test_multiple_languages_and_runtime_conflicts_remain_visible():
    result = scan_project_zip(zipped({'pyproject.toml': '[project]\nrequires-python=">=3.11"',
                                     '.python-version': '3.12', 'package.json': '{}'}))
    signals = result.architecture.components[0].signals
    assert {s.value for s in signals if s.field == 'language'} == {'Python', 'JavaScript'}
    assert 'conflicting_signals' in result.architecture.warnings


@pytest.mark.parametrize('file,content,source,entry,service,port', [
    ('go.mod', 'module api\ngo 1.23\nrequire github.com/redis/go-redis/v9 v9.0.0', 'main.go',
     'value := os.Getenv("API_TOKEN"); http.ListenAndServe(":8089", nil)', 'redis', 8089),
    ('Cargo.toml', '[package]\nname="api"\n[dependencies]\nredis="0.2"', 'src/main.rs',
     'let x = std::env::var("API_TOKEN"); listener.bind(("127.0.0.1", 8091));', 'redis', 8091),
    ('pom.xml', '<project><dependencies><dependency><artifactId>postgresql</artifactId></dependency></dependencies></project>',
     'src/main/java/ApiApplication.java', 'String x = System.getenv("API_TOKEN"); int port = 8092;', 'postgres', 8092)])
def test_remaining_languages_environment_ports_and_service_clients(file, content, source, entry, service, port):
    result = scan_project_zip(zipped({file: content, source: entry}))
    signals = result.architecture.components[0].signals
    assert any(s.field == 'environment' and s.value == 'API_TOKEN' for s in signals)
    assert any(s.field == 'port' and s.value == port and s.confidence == 'low' for s in signals)
    assert any(s.field == 'service' and s.value == service and s.confidence == 'low' for s in signals)


def test_multiple_framework_defaults_do_not_silently_select_one():
    result = scan_project_zip(zipped({'pyproject.toml': '[project]\ndependencies=["fastapi", "flask"]'}))
    assert {s.value for s in result.architecture.components[0].signals if s.field == 'port'} == {5000, 8000}
    assert 'conflicting_signals' in result.architecture.warnings
