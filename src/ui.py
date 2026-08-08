from __future__ import annotations

from typing import Literal, cast

import streamlit as st

from src.models import HeatLevel, TaskStatus

_BadgeColor = Literal[
    "red", "orange", "yellow", "blue", "green", "violet", "gray", "grey", "primary"
]

_TASK_STATUS_PRESENTATION: dict[TaskStatus, tuple[str, _BadgeColor, str]] = {
    TaskStatus.QUEUED: ("排队中", "gray", ":material/schedule:"),
    TaskStatus.SUBMITTED: ("已提交", "blue", ":material/upload:"),
    TaskStatus.RUNNING: ("处理中", "blue", ":material/pending:"),
    TaskStatus.SUCCEEDED: ("已完成", "green", ":material/check_circle:"),
    TaskStatus.FAILED: ("失败", "red", ":material/error:"),
    TaskStatus.CANCELLED: ("已取消", "gray", ":material/cancel:"),
    TaskStatus.OUTCOME_UNKNOWN: (
        "结果待核对",
        "orange",
        ":material/help_center:",
    ),
    TaskStatus.PAUSED: ("等待操作", "orange", ":material/pause_circle:"),
}


def render_page_header(title: str, description: str, *, icon: str) -> None:
    with st.container(
        horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    ):
        st.title(f":material/{icon}: {title}")
        st.badge("合规数据试点", icon=":material/database:", color="green")
    st.caption(description)


def render_heat_badge(level: HeatLevel) -> None:
    colors: dict[HeatLevel, _BadgeColor] = {
        HeatLevel.S: "red",
        HeatLevel.A: "orange",
        HeatLevel.B: "blue",
        HeatLevel.STATIC_HIGH: "violet",
        HeatLevel.ANOMALOUS: "red",
        HeatLevel.NORMAL: "gray",
        HeatLevel.INSUFFICIENT: "gray",
    }
    suffix = " 级" if level in {HeatLevel.S, HeatLevel.A, HeatLevel.B} else ""
    st.badge(f"{level.value}{suffix}", color=cast(_BadgeColor, colors[level]))


def render_task_badge(status: TaskStatus) -> None:
    label, color, icon = _TASK_STATUS_PRESENTATION.get(
        status,
        ("状态待确认", "gray", ":material/help:"),
    )
    st.badge(label, color=cast(_BadgeColor, color), icon=icon)


def task_status_label(status: TaskStatus) -> str:
    presentation = _TASK_STATUS_PRESENTATION.get(status)
    return presentation[0] if presentation else "状态待确认"
