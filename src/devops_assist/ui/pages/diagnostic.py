"""Questionnaire CALMS ; les calculs et le stockage restent hors de cette page."""

import streamlit as st

from devops_assist.calms import (
    MAX_NOTE_LENGTH,
    UNKNOWN,
    calculate_scores,
    load_questionnaire,
)
from devops_assist.db import ProjectStore
from devops_assist.models import Project
from devops_assist.ui.scan import render_scan
from devops_assist.ui.results import render_results

ANSWER_LABELS = {
    UNKNOWN: "Inconnu", 0: "0 — Absent", 1: "1 — Ponctuel",
    2: "2 — Partiel ou régulier", 3: "3 — Systématisé",
}


def percentage(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + " %"


def render(store: ProjectStore, project: Project | None) -> None:
    st.header("Diagnostic guidé")
    if project is None:
        st.warning("Ouvrez un projet avant de préparer un diagnostic.")
        st.info("Créez ou ouvrez un projet dans l'espace Projets.")
        return
    st.text(project.name)
    render_calms(store, project)
    render_scan(project.id)
    render_results(store, project.id)


def render_calms(store: ProjectStore, project: Project) -> None:
    st.subheader("Questionnaire CALMS")
    st.write(
        "Décrivez les pratiques de votre équipe dans les cinq onglets. "
        "Choisissez « Inconnu » si l'information manque. "
        "Les réponses et notes sont conservées uniquement après enregistrement."
    )
    questionnaire = load_questionnaire()
    try:
        saved = store.load_calms(project.id)
    except ValueError as error:
        st.error(str(error))
        return
    initial_answers = saved.answers if saved else {}
    initial_notes = saved.notes if saved else {}
    with st.form(f"calms_{project.id}"):
        tabs = st.tabs([dimension.label for dimension in questionnaire.dimensions])
        answers, notes = {}, {}
        for tab, dimension in zip(tabs, questionnaire.dimensions):
            with tab:
                for question in dimension.questions:
                    key = f"calms_{project.id}_{question.id}"
                    answers[question.id] = st.selectbox(
                        question.text,
                        options=tuple(ANSWER_LABELS),
                        index=tuple(ANSWER_LABELS).index(initial_answers.get(question.id, UNKNOWN)),
                        format_func=ANSWER_LABELS.get,
                        key=key,
                    )
                    notes[question.id] = st.text_area(
                        f"Note pour {question.id} (facultative)",
                        value=initial_notes.get(question.id, ""),
                        max_chars=MAX_NOTE_LENGTH,
                        height=80,
                        key=f"{key}_note",
                    )
        submitted = st.form_submit_button("Enregistrer et calculer", type="primary")
    if submitted:
        try:
            saved = store.save_calms(project.id, answers, notes)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Le questionnaire CALMS a été enregistré pour ce projet.")

    if saved is None:
        st.info("Aucun questionnaire enregistré pour ce projet.")
        return
    scores = calculate_scores(saved.answers)
    known = sum(score.answered for score in scores)
    total = len(questionnaire.question_ids)
    st.subheader("Résultats de la dernière sauvegarde")
    st.caption(
        f"Questionnaire {saved.version} · Enregistré le "
        f"{saved.saved_at.strftime('%d/%m/%Y à %H:%M')} UTC. "
        "Les modifications non enregistrées ne changent pas ces résultats."
    )
    st.write(f"Couverture totale : {known}/{total} réponses connues ({percentage(known / total)}).")
    for column, score in zip(st.columns(len(scores)), scores):
        with column:
            value = "Indéterminé" if score.score is None else f"{score.score:.1f}".replace(".", ",") + " / 100"
            st.metric(score.label, value)
            st.caption(f"{score.answered}/{score.total} réponses connues · Couverture : {percentage(score.coverage)}")
            st.write(score.interpretation)
    st.caption(
        "Score = somme des réponses connues / (3 × nombre de réponses connues) × 100. "
        "Les inconnus sont exclus du calcul. Indicateurs déclaratifs internes, "
        "sans score global ni certification officielle. Les notes restent locales."
    )
