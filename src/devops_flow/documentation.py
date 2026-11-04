"""Pages documentaires, validation et HTML restrictif indépendants de l'interface."""
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import re

MAX_PAGE_BYTES = 256 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100


@dataclass(frozen=True)
class DocumentationPage:
    id: str
    project_id: str
    version: int
    title: str
    markdown: str
    created_at: str


def validate_page(title, markdown):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(c) < 32 for c in title):
        raise ValueError('Le titre doit contenir de 1 à 120 caractères, sans caractère de contrôle.')
    if not isinstance(markdown, str) or len(markdown.encode('utf-8')) > MAX_PAGE_BYTES or '\x00' in markdown:
        raise ValueError('La page doit être un texte Markdown de 256 Kio maximum.')
    return title.strip(), markdown


ALLOWED_TAGS = frozenset({'p','br','hr','h1','h2','h3','h4','h5','h6','ul','ol','li','strong','em','del',
                          'blockquote','pre','code','table','thead','tbody','tr','th','td','a'})
VOID_TAGS = frozenset({'br','hr'})
SUPPRESSED = frozenset({'script','style','iframe','object','embed','svg','math','video','audio','picture','template'})


class _SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = []

    def handle_starttag(self, tag, attrs):
        if tag in SUPPRESSED:
            self.blocked.append(tag)
            return
        if self.blocked or tag not in ALLOWED_TAGS:
            return
        href = dict(attrs).get('href', '')
        attribute = f' href="{escape(href, quote=True)}"' if tag == 'a' and href.startswith('#') else ''
        self.parts.append(f'<{tag}{attribute}>')

    def handle_startendtag(self, tag, attrs):
        if tag in VOID_TAGS and not self.blocked:
            self.parts.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))


def sanitize_html(content):
    parser = _SafeHTML()
    parser.feed(content)
    parser.close()
    return ''.join(parser.parts)


def markdown_parts(markdown):
    """Séparer seulement les clôtures Mermaid ; les autres fences restent opaques."""
    parts, current, mermaid, fence = [], [], False, None
    for line in markdown.splitlines(keepends=True):
        opening = re.match(r'^ {0,3}(`{3,}|~{3,})([^\n]*)\s*$', line)
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
                if mermaid:
                    parts.append(('mermaid', ''.join(current)))
                    current, mermaid, fence = [], False, None
                    continue
                fence = None
            current.append(line)
        elif opening:
            marker, language = opening.groups()
            fence = marker
            if language.strip().lower() == 'mermaid':
                if current:
                    parts.append(('markdown', ''.join(current)))
                current, mermaid = [], True
            else:
                current.append(line)
        else:
            current.append(line)
    if current or mermaid:
        parts.append(('mermaid' if mermaid else 'markdown', ''.join(current)))
    if len([p for p in parts if p[0] == 'mermaid']) > 20:
        raise ValueError('Une page peut contenir au maximum 20 diagrammes Mermaid.')
    return tuple(parts)


def safe_mermaid(source):
    if ('%%{' in source or re.search(r'^\s*---', source) or len(source.encode('utf-8')) > 16 * 1024
            or '\\' in source
            or re.search(r'\b(?:click|href|image|img|icon)\b|url\s*\(|@import|https?:|file:|data:|javascript:|<[/A-Za-z!]', source, re.IGNORECASE)):
        raise ValueError('Diagramme non pris en charge : directives, liens et ressources externes interdits ; maximum 16 Kio.')
    return source
