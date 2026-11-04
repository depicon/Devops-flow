import importlib.util
from dataclasses import replace
import pytest
from devops_flow.documentation import DocumentationPage
from devops_flow.documentation_pdf import _html, export_documentation, PdfError

PAGE = DocumentationPage('p','project',1,'Procédure','# Déployer\n\nÉtape française.\n\n```mermaid\nflowchart LR\n A-->B\n```','2026-11-02')


def test_export_selection_validation():
    with pytest.raises(PdfError):export_documentation([])
    with pytest.raises(PdfError):export_documentation([PAGE]*101)
    with pytest.raises(PdfError):export_documentation([replace(PAGE,markdown='x'*(256*1024))]*9)


def test_print_html_is_sanitized_and_fallback_explicit():
    unsafe = replace(PAGE,title='<img src=x>',markdown=PAGE.markdown + '\n<script>SECRET</script><img src="file:///secret">')
    html, warnings = _html((unsafe,),fallback=True)
    assert '<script>' not in html and '<img src=' not in html and 'SECRET' not in html
    assert '&lt;img src=x&gt;' in html
    assert warnings and 'Mermaid non rendu' in html and 'A--&gt;B' in html


def test_engine_fallback_is_reported(monkeypatch):
    import devops_flow.documentation_pdf as pdf
    def unavailable(_):raise PdfError('missing')
    monkeypatch.setattr(pdf,'_playwright_pdf',unavailable)
    monkeypatch.setattr(pdf,'_weasyprint_pdf',lambda _:b'%PDF-test')
    result = export_documentation((PAGE,))
    assert result.engine == 'weasyprint' and 'Chromium indisponible' in result.warnings[0]
    assert any('Mermaid non rendu' in w for w in result.warnings)
    with pytest.raises(PdfError):export_documentation((PAGE,),engine='playwright')


@pytest.mark.skipif(importlib.util.find_spec('weasyprint') is None, reason='Extra pdf non installé')
def test_real_weasyprint_pdf_and_no_resource_access(monkeypatch):
    import devops_flow.documentation_pdf as pdf
    calls=[]
    def blocked(url,*a,**kw):calls.append(url);raise AssertionError('external request')
    monkeypatch.setattr(pdf,'_deny_resource',blocked)
    result=export_documentation((PAGE,),engine='weasyprint')
    assert result.content.startswith(b'%PDF-') and len(result.content)>1000
    assert result.engine == 'weasyprint' and result.warnings and calls == []


@pytest.mark.skipif(importlib.util.find_spec('playwright') is None, reason='Extra pdf non installé')
def test_real_playwright_pdf_with_mermaid():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if not __import__('pathlib').Path(p.chromium.executable_path).exists():pytest.skip('Chromium non installé')
    result=export_documentation((PAGE,),engine='playwright')
    assert result.engine == 'playwright' and result.content.startswith(b'%PDF-') and not result.warnings
