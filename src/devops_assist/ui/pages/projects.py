"""Création, liste et fiche d'un projet."""

import streamlit as st

from devops_assist.db import ProjectStore
from devops_assist.demo import load_demo, repository_zip
from devops_assist.models import (
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    Project,
)


def render(store: ProjectStore, active: Project | None) -> Project | None:
    st.header("Accueil / Projets")
    st.write("Créez un projet ou ouvrez un projet enregistré pour retrouver son contexte.")

    with st.form("new_project", clear_on_submit=True):
        st.subheader("Nouveau projet")
        name = st.text_input(
            "Nom du projet", max_chars=MAX_PROJECT_NAME_LENGTH, key="project_name"
        )
        description = st.text_area(
            "Description (facultative)",
            max_chars=MAX_PROJECT_DESCRIPTION_LENGTH,
            key="project_description",
        )
        submitted = st.form_submit_button("Créer le projet", type="primary")

    if submitted:
        try:
            active = store.create_project(name, description)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["active_project_id"] = active.id
            st.success("Le projet a été créé et enregistré.")

    st.subheader("Démonstration fictive")
    st.caption("Chaque chargement crée un nouveau projet fictif, son questionnaire et son diagnostic. Les projets et diagnostics existants sont conservés.")
    if st.button("Charger une démo", key="load_demo"):
        active, _ = load_demo(store)
        st.session_state["active_project_id"] = active.id
        st.success("Démo fictive enregistrée. Ouvrez Diagnostic guidé, Plan d’action ou Historique / Rapports pour l’explorer.")
    st.download_button("Télécharger le ZIP fictif", repository_zip(),
                       file_name="devops-assist-demo-fictive.zip", mime="application/zip", key="download_demo_zip")

    st.subheader("Projets enregistrés")
    projects = store.list_projects()
    if not projects:
        st.info("Aucun projet enregistré pour le moment.")
    for project in projects:
        with st.container(border=True):
            # Les saisies restent du texte, jamais du Markdown/HTML interprété.
            st.text(project.name)
            st.caption(f"Créé le {project.created_at.strftime('%d/%m/%Y à %H:%M')} UTC")
            if st.button("Ouvrir", key=f"open_{project.id}"):
                active = project
                st.session_state["active_project_id"] = project.id

    if active:
        st.subheader("Fiche du projet ouvert")
        st.text(active.name)
        st.text(active.description or "Aucune description renseignée.")
        st.caption(f"Identifiant : {active.id}")
        st.info("Ouvrez Diagnostic guidé pour CALMS, le scan du dépôt et la sauvegarde d'un diagnostic avec ses actions proposées.")
    return active
