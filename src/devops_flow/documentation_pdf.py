"""PDF documentaire en mémoire ; aucun accès au dépôt ni dépendance GUI."""
from dataclasses import dataclass
from html import escape
from importlib.metadata import distribution
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown2

from devops_flow.documentation import MAX_EXPORT_BYTES, MAX_PAGES, markdown_parts, safe_mermaid, sanitize_html, validate_page


@dataclass(frozen=True)
class PdfExport:
    content: bytes
    engine: str
    warnings: tuple[str, ...] = ()


class PdfError(ValueError):
    pass


STYLE = '''
@page { size:A4; margin:18mm; @bottom-right {content:counter(page);font-size:12px;} }
body { font:14px system-ui,sans-serif; color:#0f172a; line-height:1.6; }
h1 {font-size:26px;color:#2563eb;} h2 {font-size:21px;} h3 {font-size:17px;}
section + section {break-before:page;} h1,h2,h3 {break-after:avoid;}
pre {white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:12px;}
table {border-collapse:collapse;width:100%;} th,td {border:1px solid #64748b;padding:6px;}
svg {max-width:100%;height:auto;} .version,.notice {color:#475569;font-size:14px;}
img.guide-capture {max-width:100%;height:auto;} * {-webkit-print-color-adjust:exact;}
'''


def _html(pages, *, fallback=False):
    if not pages or len(pages) > MAX_PAGES:
        raise PdfError('Sélectionnez entre 1 et 100 pages à exporter.')
    if sum(len(p.markdown.encode('utf-8')) for p in pages) > MAX_EXPORT_BYTES:
        raise PdfError('Le contenu sélectionné dépasse 2 Mio.')
    sections, warnings = [], []
    for page in pages:
        validate_page(page.title, page.markdown)
        body = [f'<h1>{escape(page.title)}</h1>', f'<p class="version">Version {page.version} · {escape(page.created_at)}</p>']
        for kind, source in markdown_parts(page.markdown):
            if kind == 'markdown':
                body.append(sanitize_html(markdown2.markdown(source, extras=['fenced-code-blocks', 'tables'])))
            else:
                try:
                    source = safe_mermaid(source)
                    allowed = True
                except ValueError:
                    allowed = False
                if fallback or not allowed:
                    message = 'Diagramme Mermaid non rendu ; code source ci-dessous.'
                    body.extend([f'<p class="notice">{message}</p>', f'<pre>{escape(source)}</pre>'])
                    warnings.append(message)
                else:
                    body.append('<pre class="diagram-source">' + escape(source) + '</pre>')
        sections.append('<section>' + ''.join(body) + '</section>')
    html = '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Documentation</title><style>' + STYLE + '</style></head><body>'
    return html + ''.join(sections) + '</body></html>', tuple(dict.fromkeys(warnings))


RENDER_SCRIPT = '''
import {mermaid} from '/assets/index.js';
mermaid.initialize({startOnLoad:false,securityLevel:'strict',maxTextSize:16384,
  fontFamily:'system-ui, sans-serif',themeVariables:{fontSize:'16px'},
  flowchart:{htmlLabels:false,useMaxWidth:true}});
window.pdfWarnings=[];
for (const [i, element] of [...document.querySelectorAll('.diagram-source')].entries()) {
  try {
    const result = await mermaid.render('doc_pdf_'+i, element.textContent);
    const target = document.createElement('div'); target.innerHTML=result.svg;
    element.replaceWith(target);
  } catch (_) {
    const message='Diagramme Mermaid non rendu ; code source ci-dessous.';
    const notice=document.createElement('p');notice.className='notice';notice.textContent=message;
    element.before(notice);window.pdfWarnings.push(message);
    document.querySelector('#ddoc_pdf_'+i)?.remove();
  }
}
await document.fonts.ready;
window.pdfReady=true;
'''


def _playwright_pdf(html):
    from playwright.sync_api import sync_playwright, Error
    # Ressources fiables installées ; aucune importation de NiceGUI dans ce service.
    assets = Path(distribution('nicegui').locate_file('nicegui/elements/mermaid/dist')).resolve()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as error:
            raise PdfError('Chromium indisponible. Installez-le avec : python -m playwright install chromium.') from error
        try:
            context = browser.new_context(service_workers='block')
            page = context.new_page()
            page.set_default_timeout(30000)

            def serve(route):
                parsed = urlsplit(route.request.url)
                if parsed.scheme != 'https' or parsed.netloc != 'devops-flow.invalid':
                    route.abort()
                elif parsed.path == '/print':
                    route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
                elif parsed.path == '/render.mjs':
                    route.fulfill(status=200, content_type='text/javascript', body=RENDER_SCRIPT)
                elif parsed.path.startswith('/assets/'):
                    candidate = (assets / unquote(parsed.path[len('/assets/'):])).resolve()
                    if candidate.is_relative_to(assets) and candidate.is_file() and candidate.suffix in ('.mjs', '.js'):
                        route.fulfill(status=200, content_type='text/javascript', body=candidate.read_bytes())
                    else:
                        route.abort()
                else:
                    route.abort()

            page.route('**/*', serve)
            html = html.replace('</body>', '<script type="module" src="/render.mjs"></script></body>')
            page.goto('https://devops-flow.invalid/print', wait_until='load')
            page.wait_for_function('window.pdfReady === true', timeout=30000)
            warnings = tuple(dict.fromkeys(page.evaluate('window.pdfWarnings')))
            data = page.pdf(format='A4', print_background=True, prefer_css_page_size=True)
            return data, warnings
        finally:
            browser.close()


def _deny_resource(url, *args, **kwargs):
    raise ValueError('Ressource externe interdite.')


def _weasyprint_pdf(html):
    from weasyprint import HTML
    return HTML(string=html, url_fetcher=_deny_resource).write_pdf()


def export_documentation(pages, *, engine='auto'):
    pages = tuple(pages)
    html, warnings = _html(pages)
    if engine not in ('auto', 'playwright', 'weasyprint'):
        raise PdfError('Moteur PDF inconnu.')
    if engine in ('auto', 'playwright'):
        try:
            content, rendered_warnings = _playwright_pdf(html)
            return PdfExport(content, 'playwright', warnings + rendered_warnings)
        except (ImportError, OSError, PdfError):
            if engine == 'playwright':
                raise PdfError('Export Playwright indisponible. Installez les dépendances PDF et Chromium.') from None
        except Exception as error:
            # Les erreurs du moteur restent neutres et ne peuvent révéler du Markdown ou un chemin.
            if engine == 'playwright':
                raise PdfError('Le rendu PDF a échoué. Vérifiez les diagrammes et réessayez.') from None
    fallback_html, warnings = _html(pages, fallback=True)
    try:
        return PdfExport(_weasyprint_pdf(fallback_html), 'weasyprint',
                         ('Export avec WeasyPrint ; Chromium indisponible ou rendu interrompu.',) + warnings if engine == 'auto' else warnings)
    except (ImportError, OSError):
        raise PdfError('Export PDF indisponible. Installez les dépendances avec : python -m pip install -e ".[pdf]".') from None
    except Exception:
        raise PdfError('Le rendu PDF a échoué. Réduisez la sélection et réessayez.') from None
