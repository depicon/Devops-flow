"""Rendu Markdown NiceGUI ; sanitisation pure partagée avec l'export PDF."""
from nicegui import ui
from devops_flow.documentation import sanitize_html


def safe_markdown(content):
    return ui.markdown(content, sanitize=sanitize_html, extras=['fenced-code-blocks','tables']).classes('w-full break-words')
