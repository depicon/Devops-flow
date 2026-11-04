"""Sous-ensemble textuel go.mod, sans téléchargement ni résolution de module."""
import re
from devops_flow.signals import Signal
from devops_flow.signals.parsing import entry_signals, error_signal, runtime, text


class GoScanner:
    name = 'Go'
    manifests = ('go.mod',)
    allowlist = manifests

    def scan(self, files):
        result = [Signal('language', 'Go', 'high', 'go.mod', stack=self.name)]
        try:
            content = re.sub(r'//[^\n]*', '', text(files['go.mod']))
            if not re.search(r'^\s*module\s+\S+', content, re.M):
                raise ValueError
            for match in re.finditer(r'^\s*(?:go|toolchain\s+go)\s*([\d.]+)\s*$', content, re.M):
                value = runtime(match.group(1))
                if value:
                    result.append(Signal('runtime', value, 'high', 'go.mod', stack=self.name))
            for module, label in {'github.com/gin-gonic/gin': 'Gin', 'github.com/labstack/echo': 'Echo',
                                  'github.com/gofiber/fiber': 'Fiber'}.items():
                if re.search(r'(?:^|\s)' + re.escape(module) + r'(?:/v\d+)?\s+v\d', content):
                    result.append(Signal('framework', label, 'high', 'go.mod', stack=self.name))
            for module, kind in {'github.com/redis/go-redis': 'redis', 'github.com/jackc/pgx': 'postgres',
                                 'github.com/go-sql-driver/mysql': 'mysql', 'github.com/rabbitmq/amqp091-go': 'rabbitmq'}.items():
                if re.search(r'(?:^|\s)' + re.escape(module) + r'(?:/v\d+)?\s+v\d', content):
                    result.append(Signal('service', kind, 'low', 'go.mod', reason='declared_client_dependency', stack=self.name))
            result += [Signal('build_command', 'go build ./...', 'medium', 'go.mod', reason='convention', stack=self.name),
                       Signal('test_command', 'go test ./...', 'medium', 'go.mod', reason='convention', stack=self.name)]
        except (ValueError, UnicodeError):
            result.append(error_signal(self.name, 'go.mod'))
        return tuple(result) + entry_signals(files, self.name)
