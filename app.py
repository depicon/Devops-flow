"""Point d'entrée de la GUI locale : streamlit run app.py."""

import os
from pathlib import Path
import sqlite3

import streamlit as st

from devops_assist.db import ProjectStore
from devops_assist.ui.pages import diagnostic, history, projects, roadmap

ROOT = Path(__file__).resolve().parent


def main() -> None:
    st.set_page_config(page_title="DevOps Assist", page_icon="🧭", layout="wide")
    st.title("DevOps Assist")
    st.caption("Votre démarche d'amélioration DevOps, étape par étape.")
    page = st.sidebar.radio(
        "Navigation",
        ("Projets", "Diagnostic guidé", "Plan d'action", "Historique / Rapports"),
        key="navigation",
    )
    st.sidebar.caption("Projets, diagnostics, suivi des actions et rapports locaux.")

    # Surcharge réservée à la configuration locale et aux bases isolées des tests.
    db_path = os.environ.get("DEVOPS_ASSIST_DB_PATH") or ROOT / "var/devops_assist.sqlite3"
    store = ProjectStore(db_path)
    try:
        store.initialize()
        project_id = st.session_state.get("active_project_id")
        active = store.get_project(project_id) if project_id else None
        if project_id and active is None:
            st.session_state.pop("active_project_id", None)
            st.warning("Ce projet n'est plus disponible. Ouvrez un autre projet.")

        if page == "Projets":
            active = projects.render(store, active)
        elif page == "Diagnostic guidé":
            diagnostic.render(store, active)
        elif page == "Plan d'action":
            roadmap.render(store, active)
        else:
            history.render(store, active)

        st.sidebar.divider()
        st.sidebar.write("Projet ouvert")
        st.sidebar.text(active.name if active else "Aucun projet sélectionné")
    except (sqlite3.Error, OSError):
        st.error(
            "Le stockage local est inaccessible. Vérifiez les droits d'accès et "
            "l'espace disponible, puis relancez l'application."
        )


if __name__ == "__main__":
    main()
