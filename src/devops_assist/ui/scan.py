"""Contrôles de scan ; seul un résultat sans contenu brut reste en session."""

import streamlit as st

from devops_assist.scanner import DEFAULT_LIMITS, ScanError, scan_local, scan_zip

STATUS_LABELS = {
    "observed": "Observé", "risk": "Point à vérifier", "not_observed": "Non observé",
    "unknown": "À confirmer", "invalid": "Non analysable", "not_applicable": "Non applicable",
}


def render_scan(project_id: str) -> None:
    st.divider()
    st.subheader("Scan technique ciblé")
    st.write("Analyse en lecture seule des configurations Docker, Compose et GitLab CI à la racine. Aucun code n'est exécuté.")
    st.caption("GitLab CI est le scénario provisoire. ZIP : 10 Mio maximum, racine directe ou dossier enveloppe unique. Aucun fichier brut n'est sauvegardé.")
    result_key = f"scan_result_{project_id}"
    mode_key, path_key = f"scan_mode_{project_id}", f"scan_path_{project_id}"
    # Streamlit supprime les clés des widgets qui ne sont plus affichés.
    # Ce contexte séparé conserve les choix du projet pendant la navigation.
    context = st.session_state.setdefault(f"scan_context_{project_id}", {"mode": "Archive ZIP", "path": ""})
    if mode_key not in st.session_state:
        st.session_state[mode_key] = context["mode"]

    def clear_result():
        st.session_state.pop(result_key, None)

    def change_mode():
        context["mode"] = st.session_state[mode_key]
        clear_result()

    def change_path():
        context["path"] = st.session_state[path_key]
        clear_result()

    mode = st.radio("Source du dépôt", ("Archive ZIP", "Dossier local"), key=mode_key, on_change=change_mode)
    uploaded, path = None, ""
    if mode == "Archive ZIP":
        uploaded = st.file_uploader("Choisir un ZIP", type=["zip"], key=f"scan_zip_{project_id}", on_change=clear_result)
    else:
        if path_key not in st.session_state:
            st.session_state[path_key] = context["path"]
        path = st.text_input("Chemin du dossier local", key=path_key, on_change=change_path)
        st.caption("Le chemin désigne un dossier sur la machine qui lance l'application. Aucun sous-dossier n'est parcouru.")
    if st.button("Analyser le dépôt", key=f"scan_run_{project_id}"):
        clear_result()
        try:
            if mode == "Archive ZIP":
                if uploaded is None:
                    st.info("Choisissez une archive ZIP avant l'analyse.")
                elif uploaded.size > DEFAULT_LIMITS.archive_bytes:
                    raise ScanError("archive_budget")
                else:
                    st.session_state[result_key] = scan_zip(uploaded.getvalue())
            elif not path.strip():
                st.info("Renseignez le chemin du dossier avant l'analyse.")
            else:
                st.session_state[result_key] = scan_local(path)
        except ScanError:
            st.error("Analyse refusée : entrée inaccessible, non sûre ou hors limites. Vérifiez la source et les limites décrites dans le README.")
    result = st.session_state.get(result_key)
    if result is None:
        return
    st.success("Scan terminé. Ces indices statiques restent à confirmer dans votre contexte.")
    source_label = "Dossier local" if result.source == "local" else "Archive ZIP"
    st.caption(f"Source du dernier scan : {source_label}.")
    if result.source == "zip" and uploaded is None:
        st.caption("Le résultat du dernier ZIP reste disponible pour finaliser. Pour relancer l'analyse, sélectionnez à nouveau une archive.")
    st.caption(f"{result.scanner_version} · {result.rules_version} · Résultat temporaire de cette session, non historisé. Relancez l'analyse si les fichiers ont changé.")
    st.write("Configurations lues : " + (", ".join(result.files) or "aucune dans le périmètre supporté"))
    for finding in result.findings:
        st.write(f"{finding.rule_id} — {STATUS_LABELS[finding.status]}")
        st.write(finding.explanation)
        st.caption(f"Source : {finding.source}. {finding.limitation}")
        for evidence in finding.evidence:
            st.text(f"{evidence.file} · {evidence.code}" + (f" · ligne {evidence.line}" if evidence.line else ""))
