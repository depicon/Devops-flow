"""Historique figé et deux téléchargements issus du même objet d'export."""

import hashlib

import streamlit as st

from devops_assist.models import Project
from devops_assist.reports import build_export, render_json, render_markdown
from devops_assist.ui.results import render_snapshot
from devops_assist.ui.selection import select_diagnostic


def render(store, project: Project | None) -> None:
    st.header("Historique / Rapports")
    if project is None:
        st.warning("Créez ou ouvrez un projet dans l'espace Projets.")
        return
    st.text(project.name)
    diagnostic_id = select_diagnostic(store, project.id, "history")
    if diagnostic_id is None:
        return
    try:
        diagnostic, actions = store.diagnostic_state(project.id, diagnostic_id)
        data = diagnostic.snapshot.to_dict()
        st.caption(f"Diagnostic {diagnostic.id} · {diagnostic.created_at.isoformat()} · {data['schema_version']}. Consultation sans scan ni recalcul.")
        render_snapshot(data, f"history_{project.id}_{diagnostic_id}")
        st.subheader("Prévisualiser et télécharger")
        # Garder une même date pour les deux téléchargements et les reruns.
        # Une modification du suivi invalide l'objet ; aucune note dans l'empreinte.
        state = [(a.id, a.recommendation_id, a.position, a.status, a.created_at.isoformat(), a.updated_at.isoformat()) for a in actions]
        fingerprint = hashlib.sha256((diagnostic.snapshot.to_json() + repr(state)).encode()).hexdigest()
        key = f"report_{project.id}_{diagnostic_id}"
        cached = st.session_state.get(key)
        refresh = st.button("Actualiser la date du rapport", key=f"report_refresh_{diagnostic_id}")
        if cached is None or cached[0] != fingerprint or refresh:
            cached = (fingerprint, build_export(diagnostic, actions))
            st.session_state[key] = cached
        report = cached[1]
        markdown, json_text = render_markdown(report), render_json(report)
        st.caption("État du suivi exporté le " + report.to_dict()["exported_at"])
        st.info("Le rapport utilise les identifiants du projet et du diagnostic. Noms, descriptions, notes et commentaires locaux sont exclus, ainsi que les chemins et contenus bruts.")
        md_tab, json_tab = st.tabs(("Aperçu Markdown", "Aperçu JSON"))
        with md_tab:
            st.markdown(markdown)
        with json_tab:
            st.code(json_text, language="json")
        filename = f"devops-assist-{project.id}-{diagnostic_id}"
        st.download_button("Télécharger Markdown", markdown.encode("utf-8"), file_name=filename + ".md",
                           mime="text/markdown; charset=utf-8", key=f"download_md_{diagnostic_id}")
        st.download_button("Télécharger JSON", json_text.encode("utf-8"), file_name=filename + ".json",
                           mime="application/json", key=f"download_json_{diagnostic_id}")
    except ValueError as error:
        st.error(str(error))
