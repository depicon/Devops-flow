"""Sélection d'un diagnostic limitée au projet ouvert."""

import streamlit as st


def select_diagnostic(store, project_id: str, space: str):
    items = store.list_diagnostics(project_id)
    if not items:
        st.info("Aucun diagnostic enregistré. Finalisez un diagnostic dans Diagnostic guidé.")
        return None
    by_id = {item.id: item for item in items}
    key = f"{space}_diagnostic_{project_id}"
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = items[0].id
    return st.selectbox(
        "Diagnostic enregistré", list(by_id), key=key,
        format_func=lambda id: f"{by_id[id].created_at.strftime('%d/%m/%Y %H:%M:%S')} UTC · {id}",
    )
