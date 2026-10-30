"""Suivi persistant et isolé des actions d'un diagnostic."""

import streamlit as st

from devops_assist.models import Project
from devops_assist.roadmap import MAX_COMMENT_LENGTH, STATUS_LABELS
from devops_assist.ui.selection import select_diagnostic


def render(store, project: Project | None) -> None:
    st.header("Plan d'action")
    if project is None:
        st.warning("Créez ou ouvrez un projet dans l'espace Projets.")
        return
    st.text(project.name)
    diagnostic_id = select_diagnostic(store, project.id, "roadmap")
    if diagnostic_id is None:
        return
    try:
        diagnostic, actions = store.diagnostic_state(project.id, diagnostic_id)
    except ValueError as error:
        st.error(str(error))
        return
    recommendations = {r["id"]: r for r in diagnostic.snapshot.to_dict()["recommendations"]}
    st.caption("Le suivi et l'ordre sont propres à ce diagnostic. Ils ne modifient jamais ses scores ni sa priorité initiale. Les commentaires restent locaux et sont exclus des rapports.")
    if not actions:
        st.info("Aucune action proposée pour ce diagnostic ; cela ne certifie pas les pratiques.")
    for index, action in enumerate(actions):
        rec = recommendations.get(action.recommendation_id)
        if rec is None:
            st.error("Une action enregistrée est incompatible avec ce diagnostic.")
            return
        with st.container(border=True):
            st.subheader(f"{index + 1}. {rec['title']}")
            st.write(rec["description"])
            st.caption(f"{rec['id']} · Priorité initiale {rec['priority']:g} = impact {rec['impact']} / effort {rec['effort']}")
            st.text("Constats : " + ", ".join(rec["source_finding_ids"]))
            st.caption(f"Créée : {action.created_at.isoformat()} · Mise à jour : {action.updated_at.isoformat()}")
            with st.form(f"action_form_{action.id}"):
                status = st.selectbox("Statut", list(STATUS_LABELS), index=list(STATUS_LABELS).index(action.status),
                                      format_func=STATUS_LABELS.get, key=f"action_status_{action.id}")
                notes = st.text_area("Commentaire local (non exporté)", value=action.notes,
                                     max_chars=MAX_COMMENT_LENGTH, key=f"action_notes_{action.id}")
                save = st.form_submit_button("Enregistrer le suivi", key=f"action_save_{action.id}")
            if save:
                try:
                    store.update_action(project.id, diagnostic_id, action.id, status=status, notes=notes)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.rerun()
            up, down = st.columns(2)
            move_up = up.button("Monter", key=f"action_up_{action.id}", disabled=index == 0)
            move_down = down.button("Descendre", key=f"action_down_{action.id}", disabled=index == len(actions) - 1)
            if move_up or move_down:
                try:
                    store.move_action(project.id, diagnostic_id, action.id, index - 1 if move_up else index + 1)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.rerun()
