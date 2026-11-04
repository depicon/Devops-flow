"""Format Markdown/frontmatter borné ; aucun fichier exécuté ni inclusion résolue."""
from dataclasses import dataclass

import yaml

from devops_flow.profiles import EvaluationProfile, MAX_PROFILE_BYTES, ENTITY


class _ProfileLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in values if tag != 'tag:yaml.org,2002:timestamp']
        for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                raise ValueError
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class ImportPreview:
    profile: EvaluationProfile

    @property
    def question_counts(self):
        return {d.id: len(d.questions) for d in self.profile.dimensions}


def parse_markdown(content: bytes) -> ImportPreview:
    if not isinstance(content, bytes) or len(content) > MAX_PROFILE_BYTES:
        raise ValueError('Le profil doit être un fichier Markdown de 256 Kio maximum.')
    try:
        text = content.decode('utf-8', errors='strict').replace('\r\n', '\n')
    except UnicodeDecodeError:
        raise ValueError('Le fichier de profil doit être encodé en UTF-8 strict.') from None
    if ENTITY.search(text):
        raise ValueError('Les entités HTML/XML sont interdites dans un profil.')
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\n') != '---':
        raise ValueError('Le frontmatter YAML doit commencer par une ligne « --- ».')
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip('\n') == '---'), None)
    if closing is None:
        raise ValueError('La fin du frontmatter YAML (« --- ») est manquante.')
    header = ''.join(lines[1:closing])
    try:
        nodes = depth = documents = 0
        for event in yaml.parse(header, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, 'anchor', None) or getattr(event, 'tag', None):
                raise ValueError
            if isinstance(event, (yaml.events.ScalarEvent, yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                nodes += 1
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
            if isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            if isinstance(event, yaml.events.DocumentStartEvent):
                documents += 1
            if nodes > 10000 or depth > 32 or documents > 1:
                raise ValueError
        data = yaml.load(header, Loader=_ProfileLoader)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise ValueError('Frontmatter YAML invalide : structure bornée, clés uniques et sans inclusion requises.') from None
    if not isinstance(data, dict) or 'conventions' in data:
        raise ValueError('Les conventions doivent figurer dans le corps Markdown.')
    data['conventions'] = ''.join(lines[closing + 1:])
    # Une seule ligne vide sépare le frontmatter du corps à l'export.
    if data['conventions'].startswith('\n'):
        data['conventions'] = data['conventions'][1:]
    return ImportPreview(EvaluationProfile.from_dict(data))


def export_markdown(profile: EvaluationProfile) -> bytes:
    data = EvaluationProfile.from_dict(profile.to_dict()).to_dict()
    conventions = data.pop('conventions')
    content = ('---\n' + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
               + '---\n\n' + conventions).encode('utf-8')
    if len(content) > MAX_PROFILE_BYTES:
        raise ValueError('Le profil exporté dépasse la limite de 256 Kio.')
    return content
