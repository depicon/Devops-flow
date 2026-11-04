"""Régénère le guide livré depuis le Markdown et les captures de recette fiables."""
import base64
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from devops_flow.documentation import DocumentationPage
from devops_flow.documentation_pdf import _html, _playwright_pdf

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'docs/guide-utilisation.md'
    page = DocumentationPage('guide','guide',1,'devops-flow — Guide d’utilisation',source.read_text().split('\n',1)[1].lstrip(),
                             datetime.now(timezone.utc).date().isoformat())
    html, _ = _html((page,))
    html = html.replace('<title>Documentation</title>', '<title>devops-flow — Guide d’utilisation</title>')
    # Captures connues du produit fictif, jamais des chemins issus d'une page utilisateur.
    appendix = []
    for name, title in (('ergonomie-diagnostic-sombre.png','Diagnostic guidé — apparence sombre, données fictives'),
                        ('ergonomie-reglages.png','Réglages du projet et profil utilisé'),
                        ('ergonomie-mobile.png','Projet et diagnostic sur écran mobile')):
        path = ROOT / 'docs/screenshots' / name
        if path.exists():
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            appendix.append('<section><h1>' + escape(title) + '</h1><img class="guide-capture" alt="' + escape(title)
                            + '" src="data:image/png;base64,' + encoded + '"></section>')
    html = html.replace('</body>', ''.join(appendix) + '</body>')
    content, warnings = _playwright_pdf(html)
    if warnings:
        raise RuntimeError('Le guide doit être généré sans avertissement de rendu.')
    destination = ROOT / 'docs/guide-utilisation.pdf'
    destination.write_bytes(content)
    print(f'{destination} : {len(content)} octets')


if __name__ == '__main__':
    main()
