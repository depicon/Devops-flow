"""Configuration Trello locale explicite, indépendante des dépôts audités."""
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values

APP_ENV_FILE = Path(__file__).resolve().parents[3] / '.env'


@dataclass(frozen=True)
class TrelloCredentials:
    api_key: str = field(repr=False)
    token: str = field(repr=False)

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 4096
               or any(ord(c) < 32 or ord(c) == 127 for c in v)
               for v in (self.api_key, self.token)):
            raise ValueError('La configuration Trello est invalide.')


def load_credentials(*, environ: Mapping[str, str] | None = None,
                     env_file: Path | None = None) -> TrelloCredentials | None:
    """Pas de find_dotenv, interpolation, mutation d'os.environ ou recherche de dépôt."""
    environment = os.environ if environ is None else environ
    path = APP_ENV_FILE if env_file is None else env_file
    values = {}
    # Le fichier n'est lu que si au moins une variable manque dans l'environnement.
    if any(k not in environment for k in ('TRELLO_API_KEY', 'TRELLO_TOKEN')):
        try:
            if path.is_symlink():
                raise ValueError('Le fichier de configuration Trello doit être un fichier local ordinaire.')
            if path.exists():
                if not path.is_file() or path.stat().st_size > 65536:
                    raise ValueError('Le fichier de configuration Trello est invalide.')
                values = dotenv_values(path, interpolate=False)
        except (OSError, UnicodeError):
            raise ValueError('La configuration locale Trello est inaccessible.') from None
    key, token = (environment.get(k, values.get(k)) for k in ('TRELLO_API_KEY', 'TRELLO_TOKEN'))
    if not key or not token:
        return None
    return TrelloCredentials(key, token)
