"""Finalisation explicite et lecture du snapshot ; aucune requête SQL ici."""

import hashlib
from uuid import uuid4

import streamlit as st

from devops_assist.diagnostic import build_diagnostic
from devops_assist.ui.scan import STATUS_LABELS


def render_results(store, project_id: str) -> None:
    st.divider()
    st.subheader("Finaliser le diagnostic")
    st.caption("Choisissez les sources à inclure. Seuls les réponses enregistrées et le dernier scan admissible sont utilisés ; les modifications du formulaire non enregistrées sont exclues.")
    try:
        saved = store.load_calms(project_id)
    except ValueError as error:
        saved = None
        st.error(str(error))
    scan = st.session_state.get(f"scan_result_{project_id}")
    use_calms = st.checkbox("Inclure le questionnaire enregistré", value=saved is not None, key=f"include_calms_{project_id}")
    use_scan = st.checkbox("Inclure le scan de cette session", value=scan is not None, key=f"include_scan_{project_id}")
    state_key = f"diagnostic_submission_{project_id}"
    state = st.session_state.setdefault(state_key, {"key": str(uuid4()), "fingerprint": None, "saved": False})
    if st.button("Finaliser et sauvegarder", key=f"diagnostic_finalize_{project_id}", type="primary"):
        try:
            if (use_calms and saved is None) or (use_scan and scan is None):
                raise ValueError("Une source sélectionnée est indisponible. Enregistrez le questionnaire, relancez le scan ou décochez cette source.")
            snapshot = build_diagnostic(answers=saved.answers if use_calms else None, scan=scan if use_scan else None)
            fingerprint = hashlib.sha256(snapshot.to_json().encode("utf-8")).hexdigest()
            if state["fingerprint"] is not None and state["fingerprint"] != fingerprint:
                state.update(key=str(uuid4()), saved=False)
            state["fingerprint"] = fingerprint
            store.save_diagnostic(project_id, state["key"], snapshot)
            state["saved"] = True
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Diagnostic sauvegardé avec ses actions. Une nouvelle pression avec ces mêmes sources réutilise cette soumission.")
    if state["saved"] and st.button("Préparer une nouvelle soumission", key=f"diagnostic_new_{project_id}"):
        st.session_state[state_key] = {"key": str(uuid4()), "fingerprint": None, "saved": False}
        st.rerun()
    try:
        latest = store.latest_diagnostic(project_id)
    except ValueError as error:
        st.error(str(error))
        return
    if latest is None:
        st.info("Aucun diagnostic finalisé. Un questionnaire même tout inconnu, ou un scan admissible, suffit ; passer les deux sources est refusé.")
        return
    data = latest.snapshot.to_dict()
    st.subheader("Dernier diagnostic sauvegardé")
    st.caption(f"Diagnostic {latest.id} · {latest.created_at.strftime('%d/%m/%Y à %H:%M')} UTC · {data['schema_version']}. Résultats figés, indépendants du formulaire et du scan courants.")
    render_snapshot(data, project_id)
    st.caption("Consultez Plan d’action pour suivre les actions, ou Historique / Rapports pour rouvrir et exporter ce diagnostic.")


def render_snapshot(data: dict, project_id: str) -> None:
    """Afficher les résultats figés, y compris depuis l’historique, sans moteur."""
    st.text("Versions : " + ", ".join(f"{key}={value or 'passé'}" for key, value in data["versions"].items()))
    for limitation in data["limitations"]:
        st.caption(limitation)
    for score in data["scores"]:
        value = "indéterminé" if score["score"] is None else f"{score['score']:.1f}/100"
        st.write(f"{score['label']} : {value} · {score['answered']}/{score['total']} réponses connues")
    for alignment in data["alignments"]:
        st.info(alignment["explanation"])
        st.caption(alignment["limitation"] + " Liens : " + ", ".join(alignment["source_finding_ids"]))
    status = st.selectbox("Filtrer les constats par statut", ("Tous", *STATUS_LABELS),
                          format_func=lambda v: STATUS_LABELS.get(v, v), key=f"findings_status_{project_id}")
    source = st.selectbox("Filtrer par source", ("Toutes", "déclaré", "observé", "inconnu"), key=f"findings_source_{project_id}")
    visible = [f for f in data["findings"] if (status == "Tous" or f["status"] == status)
               and (source == "Toutes" or f["source"] == source)]
    st.caption(f"{len(visible)} / {len(data['findings'])} constats affichés")
    for finding in visible:
        with st.expander(f"{finding['rule_id']} · {STATUS_LABELS[finding['status']]} · {finding['source']}"):
            st.write(finding["explanation"])
            st.caption(f"ID : {finding['id']} · Catégorie : {finding['category']} · Gravité : {finding['severity'] or 'non établie'}")
            for evidence in finding["evidence"]:
                if evidence["question_id"]:
                    value = "Inconnu" if evidence["value"] == "unknown" else evidence["value"]
                    st.text(f"{evidence['question_id']} : {value}")
                else:
                    st.text(f"{evidence['file']} · {evidence['code']}" + (f" · ligne {evidence['line']}" if evidence["line"] else ""))
            st.caption(finding["limitation"])
            st.text("Recommandations : " + (", ".join(finding["recommendation_ids"]) or "aucune action automatique"))
    st.subheader("Actions proposées par priorité")
    if not data["recommendations"]:
        st.info("Aucune action automatique pour les indices et déclarations retenus. Cela ne certifie pas les pratiques.")
    for recommendation in data["recommendations"]:
        with st.expander(f"{recommendation['id']} · {recommendation['title']}"):
            st.write(recommendation["description"])
            st.write(f"Impact {recommendation['impact']} / effort {recommendation['effort']} = priorité {recommendation['priority']:g}")
            st.caption(recommendation["justification"])
            st.text("Constats liés : " + ", ".join(recommendation["source_finding_ids"]))
