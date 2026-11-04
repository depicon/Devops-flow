"""Palettes locales inspirées de GitHub ; aucune saisie libre dans le CSS."""
from types import MappingProxyType
from nicegui import ui
from devops_flow.diagram import STATUS_COLORS as DIAGRAM_COLORS

LIGHT = MappingProxyType({
    'primary': '#0969DA', 'secondary': '#6639BA', 'text': '#1F2328', 'muted': '#59636E',
    'background': '#FFFFFF', 'surface': '#F6F8FA', 'border': '#D1D9E0', 'input-border': '#818B98',
    'success': '#116329', 'warning': '#7D4E00', 'error': '#B42318',
    'accent-soft': '#DDF4FF', 'success-soft': '#DAFBE1', 'error-soft': '#FFEBE9',
})
DARK = MappingProxyType({
    'primary': '#58A6FF', 'secondary': '#C297FF', 'text': '#F0F6FC', 'muted': '#B1BAC4',
    'background': '#0D1117', 'surface': '#151B23', 'border': '#3D444D', 'input-border': '#8B949E',
    'success': '#7EE787', 'warning': '#F0C36A', 'error': '#FFB1AF',
    'accent-soft': '#152D4C', 'success-soft': '#142B20', 'error-soft': '#3A2023',
})
COLORS = LIGHT
SPACING = (4, 8, 12, 16, 24, 32, 48)
RADII = {'input': 6, 'card': 10}


def apply_theme():
    ui.colors(primary='var(--app-primary)', secondary='var(--app-secondary)',
              positive='var(--app-success)', warning='var(--app-warning)', negative='var(--app-error)')
    variables = lambda colors: ';'.join(f'--app-{key}:{value}' for key, value in colors.items())
    ui.add_css('body.body--light{' + variables(LIGHT) + '}body.body--dark{' + variables(DARK) + '}')
    ui.add_css('''
    body {--q-primary:var(--app-primary); --q-secondary:var(--app-secondary); --q-positive:var(--app-success);
      --q-warning:var(--app-warning); --q-negative:var(--app-error); background:var(--app-surface); color:var(--app-text);}
    body,.q-field,.q-btn,.q-tab,input,textarea {font-family:system-ui,sans-serif; font-size:16px;}
    .app-content {width:100%;max-width:1360px;margin:0 auto;padding:32px;gap:24px;}
    .nicegui-sub-pages {width:100%;min-width:0;}
    .app-header {background:var(--app-background);color:var(--app-text);border-bottom:1px solid var(--app-border);padding:12px 20px;gap:12px;}
    .header-brand {font-size:18px;font-weight:700;letter-spacing:-.3px;}
    .project-context {border-left:1px solid var(--app-border);padding-left:16px;gap:0;min-width:0;flex:1;}
    .project-name {font-weight:650;overflow-wrap:anywhere;}
    .theme-choice {width:150px;}
    .q-drawer {background:var(--app-background);color:var(--app-text);}
    .q-btn.app-nav {width:100%;justify-content:flex-start;border-radius:6px;color:var(--app-text) !important;}
    .q-btn.app-nav.active {background:var(--app-accent-soft);color:var(--app-primary) !important;font-weight:650;}
    .profile-badge {font-size:14px;padding:4px 10px;border:1px solid var(--app-border);border-radius:20px;background:var(--app-surface);color:var(--app-text);overflow-wrap:anywhere;}
    .page-heading {font-size:28px;font-weight:700;letter-spacing:-.6px;line-height:1.3;}
    .section-heading {font-size:19px;font-weight:650;line-height:1.4;}
    .caption {font-size:14px;line-height:1.6;color:var(--app-muted);}
    .app-card {width:100%;padding:24px;border:1px solid var(--app-border);border-radius:10px;box-shadow:0 1px 0 rgb(0 0 0 / .04);gap:16px;}
    .q-card,.q-menu {background:var(--app-background);color:var(--app-text);}
    .q-btn {text-transform:none;min-height:40px;font-weight:550;border-radius:6px;}
    .q-btn.app-secondary {color:var(--app-text) !important;background:var(--app-background) !important;}
    .app-secondary:before {border-color:var(--app-input-border) !important;}
    .q-btn.app-primary {background:#0969DA !important;color:#FFFFFF !important;}
    .q-btn.app-primary:hover {background:#0550AE !important;}
    .q-uploader .q-uploader__header {background:#0969DA !important;color:#FFFFFF;}
    .q-uploader__header .q-btn {color:#FFFFFF !important;}
    .q-tab {text-transform:none;min-height:44px;opacity:1;}
    .q-tab--inactive {color:var(--app-muted);}
    .q-tab-panels,.q-panel {background:transparent;}
    .q-field {min-width:0;}
    .q-field__native,.q-field__input,.q-field__marginal {color:var(--app-text);font-size:16px;}
    .q-field--outlined .q-field__control:before {border-color:var(--app-input-border);}
    .q-field__label {color:var(--app-muted);font-size:14px;}
    .q-field--float .q-field__label {font-size:18.666667px;}
    .q-expansion-item {width:100%;border:1px solid var(--app-border);border-radius:6px;}
    .q-expansion-item__content {padding:16px;}
    .q-item__label,.q-tab__label {font-size:16px;line-height:1.5;}
    .q-notification,.q-tooltip {font-size:14px;}
    .literal {white-space:pre-wrap;overflow-wrap:anywhere;}
    .kpi {flex:1 1 150px;border-left:3px solid var(--app-primary);padding:0 12px;}
    .kpi-value {font-size:24px;font-weight:650;}
    .feedback {width:100%;padding:16px;border:1px solid var(--app-border);border-radius:8px;background:var(--app-accent-soft);color:var(--app-text);}
    .feedback.error {background:var(--app-error-soft);color:var(--app-error);}
    .feedback.success {background:var(--app-success-soft);color:var(--app-success);}
    .next-action {border-left:4px solid var(--app-primary);}
    .status-pill {font-size:14px;padding:4px 10px;border-radius:20px;background:var(--app-surface);color:var(--app-muted);}
    :is(a,button,input,textarea,[tabindex]):focus-visible {outline:3px solid var(--app-primary);outline-offset:3px;}
    .q-field:focus-within {outline:3px solid var(--app-primary);outline-offset:3px;border-radius:6px;}
    .q-radio:focus-within,.q-checkbox:focus-within {outline:3px solid var(--app-primary);}
    .responsive-row {width:100%;align-items:stretch;gap:24px;}
    .responsive-row > .q-card {flex:1 1 320px;}
    a {color:var(--app-primary);}
    .q-markdown {color:var(--app-text);}
    .q-markdown pre,.q-markdown code {background:var(--app-surface);color:var(--app-text);}
    .flow-layout {display:flex;width:100%;gap:24px;align-items:flex-start;}
    .flow-graphic {flex:1 1 0;min-width:0;}
    .flow-scroll {width:100%;overflow-x:auto;border:1px solid var(--app-border);border-radius:8px;background:var(--app-background);color:var(--app-text);}
    .flow-mermaid svg .cluster rect {fill:var(--app-surface) !important;stroke:var(--app-primary) !important;}
    .flow-mermaid svg .cluster-label text {fill:var(--app-text) !important;}
    .flow-mermaid svg .flowchart-link {stroke:var(--app-muted) !important;}
    .flow-mermaid svg marker path {fill:var(--app-muted) !important;stroke:var(--app-muted) !important;}
    .flow-mermaid {padding:16px;}
    .flow-mermaid:empty::before {content:'Rendu du diagramme…';font-size:16px;color:#59636E;}
    .flow-mermaid svg {max-width:none !important;}
    .flow-trace {flex:0 0 300px;min-width:0;overflow-wrap:anywhere;}
    .flow-legend {display:flex;flex-wrap:wrap;gap:8px;}
    .flow-legend .q-btn {font-size:14px;padding:8px;}
    .diagnostic-steps {border-bottom:1px solid var(--app-border);}
    .q-uploader__subtitle,.q-uploader__file-subtitle {font-size:14px;}
    .q-btn.disabled {opacity:.7 !important;}
    @media(max-width:1000px) {.flow-layout {flex-direction:column;} .flow-graphic,.flow-trace {width:100%;flex:auto;}}
    @media(max-width:640px) {
      .app-content {padding:24px 16px;} .app-card {padding:16px;}
      .app-header {padding:8px 12px;gap:8px;} .header-brand {font-size:16px;}
      .project-context {order:3;flex-basis:100%;border-left:0;border-top:1px solid var(--app-border);padding:8px 0 0;}
      .theme-choice {margin-left:auto;width:138px;} .header-project-actions {order:4;width:100%;}
      .page-heading {font-size:24px;} .responsive-row {gap:16px;}
    }
    ''')


def theme_control():
    """Préférence locale au navigateur ; aucun jeton ni état métier n'y est stocké."""
    dark = ui.dark_mode(None)
    def change(event):
        dark.value = {'system': None, 'light': False, 'dark': True}[event.value]
        # Valeurs fermées ; aucune donnée utilisateur interpolée dans ce script.
        ui.run_javascript('try { localStorage.setItem("devops-flow-theme", ' + repr(event.value) + '); } catch {}')
    from devops_flow.ui.components import marked
    selector = marked(ui.select({'system':'Système','light':'Clair','dark':'Sombre'}, value='system',
                               label='Apparence', on_change=change).props('outlined dense').classes('theme-choice'), 'theme_mode')
    selector.on('theme-restore', lambda event: selector.set_value(event.args) if event.args in ('system','light','dark') else None)
    ui.timer(.1, lambda: ui.run_javascript('''try {
        const mode = localStorage.getItem('devops-flow-theme') ?? localStorage.getItem('devops-assist-theme');
        if (['system','light','dark'].includes(mode)) getElement(''' + str(selector.id) + ''').$emit('theme-restore', mode);
    } catch {}'''), once=True)
    return dark
