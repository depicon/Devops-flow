"""Signaux statiques contrôlés : aucune lecture, exécution ou dépendance UI."""
from dataclasses import dataclass
import re
from typing import Mapping, Protocol

SIGNALS_VERSION = 'signals-v1'
CONFIDENCES = ('high', 'medium', 'low', 'manual', 'unknown')
FIELDS = ('language', 'runtime', 'framework', 'entrypoint', 'port', 'environment',
          'service', 'build_command', 'test_command')


@dataclass(frozen=True)
class Signal:
    field: str
    value: str | int | None
    confidence: str
    file: str | None = None
    line: int | None = None
    reason: str = 'declaration'
    stack: str | None = None

    def __post_init__(self):
        if self.field not in FIELDS or self.confidence not in CONFIDENCES:
            raise ValueError('Signal non pris en charge.')
        if self.value is not None and not (type(self.value) is int or isinstance(self.value, str)):
            raise ValueError('Valeur de signal invalide.')
        if self.field != 'port' and self.value is not None and not isinstance(self.value, str):
            raise ValueError('Un signal textuel doit contenir du texte.')
        if isinstance(self.value, str) and (len(self.value) > 160 or any(ord(c) < 32 for c in self.value)):
            raise ValueError('Valeur de signal hors limites.')
        if self.field == 'port' and self.value is not None and not (type(self.value) is int and 1 <= self.value <= 65535):
            raise ValueError('Port invalide.')
        if self.field == 'environment' and self.value is not None and not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', str(self.value)):
            raise ValueError('Nom de variable invalide.')
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise ValueError('Ligne invalide.')


class StackScanner(Protocol):
    name: str
    manifests: tuple[str, ...]
    allowlist: tuple[str, ...]

    def scan(self, files: Mapping[str, bytes]) -> tuple[Signal, ...]: ...


class ScannerRegistry:
    def __init__(self, scanners: tuple[StackScanner, ...] = ()):
        self.scanners = tuple(scanners)
        if len({s.name for s in scanners}) != len(scanners):
            raise ValueError('Scanner déjà enregistré.')

    def scan(self, files: Mapping[str, bytes]) -> tuple[Signal, ...]:
        results = []
        for scanner in self.scanners:
            if set(files) & set(scanner.manifests):
                results.extend(scanner.scan(files))
        results = list(dict.fromkeys(results))
        if len(results) > 256:
            raise ValueError('Trop de signaux dans un composant.')
        defaults = {'Django': 8000, 'FastAPI': 8000, 'Flask': 5000, 'Next.js': 3000,
                    'Nuxt': 3000, 'Spring Boot': 8080, 'Quarkus': 8080, 'Micronaut': 8080}
        port_stacks = {s.stack for s in results if s.field == 'port'}
        for signal in tuple(results):
            if (signal.field == 'framework' and signal.value in defaults
                    and signal.stack not in port_stacks):
                results.append(Signal('port', defaults[signal.value], 'medium', signal.file,
                    reason='framework_convention', stack=signal.stack))
        return tuple(dict.fromkeys(results))
