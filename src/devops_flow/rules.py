"""Six contrôles heuristiques purs ; aucune valeur de configuration en sortie."""

from dataclasses import dataclass
import re

import yaml
from yaml.events import AliasEvent, MappingStartEvent, SequenceStartEvent, MappingEndEvent, SequenceEndEvent, ScalarEvent

RULES_VERSION = "rules-v3"
YAML_MAX_DEPTH = 32
YAML_MAX_NODES = 10_000
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
# Seuls ces types, jamais les références d'image ou noms de service, sortent du parseur.
SERVICE_IMAGES = {"postgres": "postgres", "redis": "redis", "mysql": "mysql",
                  "rabbitmq": "rabbitmq", "memcached": "cache", "mariadb": "database", "nats": "queue"}
SERVICE_TYPES = frozenset(SERVICE_IMAGES.values())


def _service_type(image):
    if not isinstance(image, str) or not image or "$" in image:
        return "unresolved"
    name = image.split("@", 1)[0].split(":", 1)[0]
    for prefix in ("docker.io/library/", "docker.io/", "library/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return SERVICE_IMAGES.get(name, "unresolved")
CI_RESERVED = frozenset({
    "include", "stages", "variables", "workflow", "default", "image", "services",
    "cache", "before_script", "after_script", "spec", "types",
})


@dataclass(frozen=True)
class Evidence:
    file: str
    code: str
    line: int | None = None


@dataclass(frozen=True)
class TechnicalFinding:
    rule_id: str
    status: str
    source: str
    explanation: str
    evidence: tuple[Evidence, ...]
    limitation: str


class InvalidConfiguration(ValueError):
    pass


class UnsupportedConfiguration(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise UnsupportedConfiguration()
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise InvalidConfiguration()
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _yaml_document(content: bytes):
    try:
        text = content.decode("utf-8")
        depth = nodes = documents = 0
        # Borner avant de construire les objets Python ; aucun alias accepté en V1.
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, AliasEvent):
                raise InvalidConfiguration()
            if isinstance(event, (ScalarEvent, MappingStartEvent, SequenceStartEvent)):
                nodes += 1
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
            if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                depth -= 1
            if isinstance(event, yaml.events.DocumentStartEvent):
                documents += 1
            if nodes > YAML_MAX_NODES or depth > YAML_MAX_DEPTH or documents > 1:
                raise InvalidConfiguration()
        value = yaml.load(text, Loader=_UniqueLoader)
        if not isinstance(value, dict):
            raise InvalidConfiguration()
        return value
    except (InvalidConfiguration, UnsupportedConfiguration):
        raise
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError, OverflowError):
        raise InvalidConfiguration() from None


def _finding(rule, status, explanation, evidence=(), limitation=""):
    return TechnicalFinding(rule, status, "inconnu" if status in {
        "unknown", "invalid", "not_observed", "not_applicable"
    } else "observé", explanation, tuple(evidence), limitation)


def _docker(content: bytes):
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise InvalidConfiguration() from None
    instructions, pending, start = [], "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            if re.match(r"#\s*escape\s*=\s*`", line, re.I):
                raise UnsupportedConfiguration()
            continue
        if not pending:
            start = number
        pending += line[:-1] + " " if line.endswith("\\") else line
        if line.endswith("\\"):
            continue
        match = re.fullmatch(r"([A-Za-z]+)\s+(.+)", pending)
        pending = ""
        if not match:
            raise InvalidConfiguration()
        command, argument = match[1].upper(), match[2]
        if "<<" in argument:
            raise UnsupportedConfiguration()
        if command not in {
            "FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV", "ADD", "COPY",
            "ENTRYPOINT", "VOLUME", "USER", "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL",
            "HEALTHCHECK", "SHELL",
        }:
            raise UnsupportedConfiguration()
        instructions.append((command, argument, start))
    if pending:
        raise InvalidConfiguration()
    images, last_user = [], None
    for command, argument, line in instructions:
        if command == "FROM":
            match = re.fullmatch(r"(?:--platform=\S+\s+)?(\S+)(?:\s+[Aa][Ss]\s+[A-Za-z0-9_.-]+)?", argument)
            if not match:
                raise InvalidConfiguration()
            images.append((match[1], line))
            last_user = None
        elif not images and command != "ARG":
            raise InvalidConfiguration()
        elif command == "USER":
            last_user = line
    if not images:
        raise InvalidConfiguration()
    return images, last_user


def _docker_findings(files):
    limit = "Indice statique uniquement ; aucun build. Les tags ne garantissent pas l'immuabilité."
    user_limit = "L'utilisateur effectif dépend aussi de l'image et du lancement ; USER ne prouve pas la sécurité."
    if "Dockerfile" not in files:
        return [
            _finding("DOC-001", "not_observed", "Dockerfile non observé à la racine supportée.", limitation="Docker peut être utilisé ailleurs ; besoin à confirmer."),
            *[_finding(rule, "not_applicable", "Dockerfile non fourni.") for rule in ("DOC-002", "DOC-003")],
        ]
    presence = _finding("DOC-001", "observed", "Dockerfile trouvé dans le périmètre.", [Evidence("Dockerfile", "file_found")], "La présence ne prouve ni build ni usage réel.")
    try:
        images, user = _docker(files["Dockerfile"])
    except (InvalidConfiguration, UnsupportedConfiguration) as error:
        status = "invalid" if isinstance(error, InvalidConfiguration) else "unknown"
        return [presence, *[_finding(rule, status, "Dockerfile non analysable ou syntaxe à confirmer.",
                                    [Evidence("Dockerfile", "docker_" + status)], limit)
                            for rule in ("DOC-002", "DOC-003")]]
    latest = [Evidence("Dockerfile", "explicit_latest", line) for image, line in images
              if image.endswith(":latest")]
    variable = any("$" in image for image, _ in images)
    image_status = "risk" if latest else "unknown" if variable else "observed"
    return [presence,
            _finding("DOC-002", image_status, {
                "risk": "Tag latest explicite observé dans une instruction FROM.",
                "unknown": "Référence d'image variable : version à confirmer.",
                "observed": "Aucun tag latest explicite dans les FROM analysés.",
            }[image_status], latest or [Evidence("Dockerfile", "image_variable" if variable else "no_explicit_latest")], limit),
            _finding("DOC-003", "observed" if user else "risk",
                     "Instruction USER explicite dans le dernier stage." if user else "Aucune instruction USER active dans le dernier stage ; utilisateur à vérifier.",
                     [Evidence("Dockerfile", "final_user" if user else "no_final_user", user)], user_limit)]


def _compose_status(content):
    doc = _yaml_document(content)
    services = doc.get("services")
    if not isinstance(services, dict):
        raise InvalidConfiguration()
    risk = invalid = False
    service_types = set()
    uncertain = "include" in doc or "extends" in doc
    for service in services.values():
        if not isinstance(service, dict):
            invalid = True
            continue
        if "image" in service:
            service_types.add(_service_type(service["image"]))
        if "privileged" in service:
            if type(service["privileged"]) is not bool:
                invalid = True
            else:
                risk |= service["privileged"]
        uncertain |= "extends" in service
    status = "risk" if risk else "invalid" if invalid else "unknown" if uncertain else "observed"
    partial_codes = tuple(code for flag, code, primary in (
        (invalid, "invalid_compose", "invalid"), (uncertain, "compose_unresolved", "unknown"),
    ) if flag and status != primary)
    return status, partial_codes, tuple(sorted(service_types))


def _compose_finding(files):
    evidence, statuses = [], []
    for name in COMPOSE_NAMES:
        if name not in files:
            continue
        try:
            status, partial_codes, service_types = _compose_status(files[name])
        except InvalidConfiguration:
            status, partial_codes, service_types = "invalid", (), ()
        except UnsupportedConfiguration:
            status, partial_codes, service_types = "unknown", (), ()
        statuses.append(status)
        evidence.append(Evidence(name, {"risk": "privileged_true", "observed": "no_privileged_true",
                                        "unknown": "compose_unresolved", "invalid": "invalid_compose"}[status]))
        evidence.extend(Evidence(name, code) for code in partial_codes)
        evidence.extend(Evidence(name, "service_type_" + kind) for kind in service_types)
    # Signaler un risque explicite même si une autre variante n'est pas analysable.
    status = next((s for s in ("risk", "invalid", "unknown", "observed") if s in statuses), "not_applicable")
    return _finding("CMP-001", status, {
        "risk": "Booléen privileged: true observé ; privilèges à vérifier.",
        "invalid": "Au moins une configuration Compose est non analysable.",
        "unknown": "Fusion ou extension Compose non résolue : à confirmer.",
        "observed": "Aucun booléen privileged: true dans les configurations simples analysées.",
        "not_applicable": "Aucune configuration Compose fournie.",
    }[status], evidence, "Variantes analysées séparément, sans fusion ni lancement. Les preuves indiquent les analyses partielles ; aucun nom de service n'est exposé.")


def _ci_signals(content):
    doc = _yaml_document(content)
    # Types des principales clés globales du sous-ensemble pris en charge.
    for name in ("variables", "workflow", "default"):
        if name in doc and not isinstance(doc[name], dict):
            raise InvalidConfiguration()
    for name in ("stages", "types", "before_script", "after_script", "services"):
        if name in doc and not isinstance(doc[name], list):
            raise InvalidConfiguration()
    signals = set()
    for name, job in doc.items():
        if name in CI_RESERVED:
            continue
        if not isinstance(job, dict):
            raise InvalidConfiguration()
        if "stage" in job and not isinstance(job["stage"], str):
            raise InvalidConfiguration()
        if "script" in job:
            script = job["script"]
            if isinstance(script, list) and any(isinstance(item, list) for item in script):
                # Les tableaux de commandes imbriqués valides dépassent notre sous-ensemble.
                raise UnsupportedConfiguration()
            if not (isinstance(script, str) and script.strip() or
                    isinstance(script, list) and script and all(isinstance(item, str) for item in script)):
                raise InvalidConfiguration()
        if name.startswith(".") or "script" not in job:
            continue
        # Héritage/conditions non résolus : un job simple identifiable reste un indice.
        for practice, names in (("test", {"test", "tests"}), ("build", {"build"}), ("deploy", {"deploy"})):
            if job.get("stage", "").casefold() == practice or name.casefold() in names:
                signals.add(practice)
    return signals


def _ci_status(content):
    return "observed" if "test" in _ci_signals(content) else "unknown"


def _ci_findings(files):
    name = ".gitlab-ci.yml"
    limit = "GitLab CI provisoire uniquement. Includes, héritage et conditions non résolus ; commandes non exécutées, succès des tests inconnu."
    if name not in files:
        return [_finding("CI-001", "not_observed", "CI GitLab non observée dans ce périmètre.", limitation="Fournisseur et emplacement de la CI à confirmer."),
                _finding("CI-002", "not_applicable", "Configuration GitLab CI non fournie.", limitation=limit)]
    try:
        signals = _ci_signals(files[name])
        status = "observed" if "test" in signals else "unknown"
    except InvalidConfiguration:
        status, signals = "invalid", None
    except UnsupportedConfiguration:
        status, signals = "unknown", None
    job_evidence = [Evidence(name, ("local_" + practice + "_job") if practice in signals
                             else practice + "_job_unconfirmed") for practice in ("build", "deploy")] if signals is not None else []
    return [_finding("CI-001", "observed", "Configuration GitLab CI trouvée.", [Evidence(name, "file_found")], limit),
            _finding("CI-002", status, {
                "observed": "Job local de test identifiable ; exécution réelle à confirmer.",
                "unknown": "Job de test non identifiable dans le sous-ensemble supporté : à confirmer.",
                "invalid": "Configuration GitLab CI non analysable.",
            }[status], [Evidence(name, {"observed": "local_test_job", "unknown": "test_job_unconfirmed", "invalid": "invalid_ci"}[status]), *job_evidence], limit)]


def evaluate_files(files: dict[str, bytes]) -> tuple[TechnicalFinding, ...]:
    return tuple([*_docker_findings(files), _compose_finding(files), *_ci_findings(files)])


def select_findings(findings, *, profile=None):
    """Sélection pure après observation ; ne modifie jamais les contrôles du scanner."""
    if profile is None:
        return tuple(findings)
    return tuple(f for f in findings if f.rule_id in profile.rule_ids)
