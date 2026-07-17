from __future__ import annotations

import streamlit as st

from src.models import HeatLevel, TaskStatus


def render_page_header(title: str, description: str, *, icon: str) -> None:
    with st.container(
        horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    ):
        st.title(f":material/{icon}: {title}")
        st.badge("Mock 框架", icon=":material/science:", color="blue")
    st.caption(description)


def render_heat_badge(level: HeatLevel) -> None:
    colors = {
        HeatLevel.S: "red",
        HeatLevel.A: "orange",
        HeatLevel.B: "blue",
        HeatLevel.NORMAL: "gray",
        HeatLevel.INSUFFICIENT: "gray",
    }
    st.badge(f"{level.value} 级", color=colors[level])


def render_task_badge(status: TaskStatus) -> None:
    labels = {
        TaskStatus.QUEUED: ("排队中", "gray", ":material/schedule:"),
        TaskStatus.RUNNING: ("处理中", "blue", ":material/pending:"),
        TaskStatus.SUCCEEDED: ("已完成", "green", ":material/check_circle:"),
        TaskStatus.FAILED: ("失败", "red", ":material/error:"),
    }
    label, color, icon = labels[status]
    st.badge(label, color=color, icon=icon)
