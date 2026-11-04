"""Référentiels immuables et validation pure, sans accès GUI ni stockage."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import re

from devops_flow.calms import Dimension, Question, Questionnaire, load_questionnaire
from devops_flow.models import TECHNICAL_RULE_IDS

FORMAT_VERSION = 'devops-assist-profile-v1'
MAX_PROFILE_BYTES = 256 * 1024
MAX_CONVENTIONS_BYTES = 128 * 1024
IDENTIFIER = re.compile(r'[a-z0-9-]{1,64}\Z')
ENTITY = re.compile(r'&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);|<!\s*(?:DOCTYPE|ENTITY)', re.I)
DIMENSION_LABELS = dict(zip('CALMS', ('Culture', 'Automation', 'Lean', 'Measurement', 'Sharing')))


def _require(condition, field):
    if not condition:
        raise ValueError(f'Profil invalide : {field}.')


def _string(value, maximum, field, *, required=False):
    _require(isinstance(value, str) and len(value) <= maximum and '\x00' not in value
             and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
             and (not required or bool(value.strip())) and not ENTITY.search(value), field)
    return value


@dataclass(frozen=True)
class RuleConfig:
    id: str
    impact: int | None = None
    effort: int | None = None

    def to_dict(self):
        return {k: v for k, v in (('id', self.id), ('impact', self.impact), ('effort', self.effort)) if v is not None}


@dataclass(frozen=True)
class EvaluationProfile:
    slug: str
    version: int
    name: str
    description: str
    author: str
    created_at: str
    dimensions: tuple[Dimension, ...]
    risk_threshold: float
    min_coverage: float
    rules: tuple[RuleConfig, ...]
    conventions: str
    is_current: bool = True
    is_active: bool = True

    @property
    def questionnaire(self):
        return Questionnaire(f'{self.slug}-v{self.version}', self.dimensions)

    @property
    def question_ids(self):
        return self.questionnaire.question_ids

    @property
    def rule_ids(self):
        return tuple(r.id for r in self.rules)

    def to_dict(self):
        return {
            'format': FORMAT_VERSION, 'slug': self.slug, 'version': self.version,
            'name': self.name, 'description': self.description, 'author': self.author,
            'created_at': self.created_at,
            'calms': {'questions': {d.id: [dict(id=q.id, text=q.text, **({'help': q.help} if q.help else {}))
                                           for q in d.questions] for d in self.dimensions},
                      'risk_threshold': self.risk_threshold, 'min_coverage': self.min_coverage},
            'rules': [r.to_dict() for r in self.rules], 'conventions': self.conventions,
        }

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw):
        try:
            return cls.from_dict(json.loads(raw))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError('Le profil enregistré est illisible ou incompatible.') from None

    @classmethod
    def from_dict(cls, data):
        _require(isinstance(data, dict) and set(data) == {
            'format', 'slug', 'version', 'name', 'description', 'author', 'created_at', 'calms', 'rules', 'conventions'},
            'champs attendus ou format')
        _require(data['format'] == FORMAT_VERSION, 'version du format non prise en charge')
        _require(isinstance(data['slug'], str) and IDENTIFIER.fullmatch(data['slug'])
                 and data['slug'] not in {'new', 'import'}, 'slug (1 à 64 caractères a-z, 0-9, tiret)')
        _require(type(data['version']) is int and 1 <= data['version'] <= 2147483647, 'version entière positive bornée')
        for field, maximum, required in (('name', 120, True), ('description', 2000, False), ('author', 120, False)):
            _string(data[field], maximum, field, required=required)
        _string(data['created_at'], 40, 'date')
        try:
            when = datetime.fromisoformat(data['created_at'])
            _require(when.utcoffset() is not None, 'date avec fuseau horaire')
        except (ValueError, TypeError):
            raise ValueError('Profil invalide : date ISO 8601 avec fuseau horaire attendue.') from None
        calms = data['calms']
        _require(isinstance(calms, dict) and set(calms) == {'questions', 'risk_threshold', 'min_coverage'}, 'configuration CALMS')
        for field, maximum in (('risk_threshold', 100), ('min_coverage', 1)):
            value = calms[field]
            _require(type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value), field)
        questions = calms['questions']
        _require(isinstance(questions, dict) and set(questions) == set('CALMS'), 'cinq dimensions C/A/L/M/S requises')
        dimensions, seen = [], set()
        for dimension in 'CALMS':
            entries = questions[dimension]
            _require(isinstance(entries, list) and 1 <= len(entries) <= 10, '1 à 10 questions par dimension')
            result = []
            for entry in entries:
                _require(isinstance(entry, dict) and {'id', 'text'} <= set(entry) <= {'id', 'text', 'help'}, 'champs de question')
                qid = entry['id']
                _require(isinstance(qid, str) and IDENTIFIER.fullmatch(qid) and qid not in seen, 'identifiant de question unique et borné')
                seen.add(qid)
                result.append(Question(qid, _string(entry['text'], 1000, 'texte de question', required=True),
                                       _string(entry.get('help', ''), 2000, 'aide de question')))
            dimensions.append(Dimension(dimension, DIMENSION_LABELS[dimension], tuple(result)))
        _require(isinstance(data['rules'], list) and len(data['rules']) <= 6, 'liste de règles')
        rules, seen = [], set()
        for entry in data['rules']:
            _require(isinstance(entry, dict) and {'id'} <= set(entry) <= {'id', 'impact', 'effort'}, 'champs de règle')
            rid = entry['id']
            _require(isinstance(rid, str) and rid in TECHNICAL_RULE_IDS and rid not in seen, 'règle connue et unique')
            seen.add(rid)
            for field in ('impact', 'effort'):
                _require(field not in entry or (type(entry[field]) is int and 1 <= entry[field] <= 3), 'poids entier de 1 à 3')
            rules.append(RuleConfig(rid, entry.get('impact'), entry.get('effort')))
        conventions = _string(data['conventions'], MAX_CONVENTIONS_BYTES, 'conventions')
        _require(len(conventions.encode('utf-8')) <= MAX_CONVENTIONS_BYTES, 'conventions limitées à 128 Kio')
        profile = cls(data['slug'], data['version'], data['name'], data['description'], data['author'], data['created_at'],
                      tuple(dimensions), calms['risk_threshold'], calms['min_coverage'], tuple(rules), conventions)
        _require(len(profile.to_json().encode('utf-8')) <= MAX_PROFILE_BYTES, 'profil limité à 256 Kio')
        return profile

    def published(self, version):
        return replace(self, version=version, created_at=datetime.now(timezone.utc).isoformat(), is_current=True, is_active=True)


def baseline_profile():
    """Copie canonique du catalogue historique, sans changer ses ressources."""
    return EvaluationProfile.from_dict({
        'format': FORMAT_VERSION, 'slug': 'baseline', 'version': 1, 'name': 'Référentiel de base',
        'description': 'Questionnaire CALMS et six contrôles techniques de référence.', 'author': 'DevOps Assist',
        'created_at': '2026-11-02T00:00:00+00:00',
        'calms': {'questions': {d.id: [{'id': q.id.lower(), 'text': q.text} for q in d.questions]
                                for d in load_questionnaire().dimensions},
                  'risk_threshold': 50, 'min_coverage': 2 / 3},
        'rules': [{'id': rule} for rule in TECHNICAL_RULE_IDS], 'conventions': '',
    })


def changed_sections(before, after):
    left, right = before.to_dict(), after.to_dict()
    sections = []
    if left['calms']['questions'] != right['calms']['questions']:
        sections.append('CALMS')
    if (before.risk_threshold, before.min_coverage) != (after.risk_threshold, after.min_coverage):
        sections.append('Seuils')
    for field, label in (('rules', 'Règles'), ('conventions', 'Conventions')):
        if left[field] != right[field]:
            sections.append(label)
    if any(left[k] != right[k] for k in ('name', 'description', 'author')):
        sections.append('Métadonnées')
    return tuple(sections)
