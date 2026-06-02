from __future__ import annotations

import csv
import html
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import streamlit as st


ROOT = Path(__file__).resolve().parent
TASK_PATH = ROOT / "data" / "annotation_tasks" / "tasks.jsonl"
DB_PATH = ROOT / "human_validation" / "streamlit_annotations.sqlite"

TASK_NAMES = {
    "weak_label_audit": "弱标签审核",
    "model_output_review": "模型输出判断",
    "teaching_case_review": "教学案例可用性",
}

ROLE_NAMES = {
    "TADV": "时间词语作状语",
    "TIME_ATTR": "时间定语",
    "DUR_COMP": "时量/补语",
    "FREQ_ADV": "频率副词",
    "TIME_NON_ADV": "非状语时间表达",
    "NONE": "无相关时间表达",
    "UNCONFIRMED": "待确认",
}


st.set_page_config(
    page_title="时间词语作状语教师评审",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(show_spinner=False)
def load_tasks(path: str) -> list[dict[str, Any]]:
    task_file = Path(path)
    with task_file.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db() -> None:
    con = connect()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS annotators (
                annotator_id TEXT PRIMARY KEY,
                display_name TEXT,
                professional_role TEXT,
                experience_years TEXT,
                consent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                annotation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                annotator_id TEXT NOT NULL,
                judgement TEXT NOT NULL,
                span_correct TEXT,
                role_correct TEXT,
                anchor_correct TEXT,
                usefulness INTEGER,
                confidence INTEGER,
                corrected_span TEXT,
                corrected_role TEXT,
                notes TEXT,
                payload TEXT NOT NULL,
                task_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, annotator_id)
            )
            """
        )
        con.commit()
    finally:
        con.close()


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def save_annotator(payload: dict[str, Any]) -> None:
    now = timestamp()
    con = connect()
    try:
        con.execute(
            """
            INSERT INTO annotators
                (annotator_id, display_name, professional_role, experience_years, consent, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(annotator_id) DO UPDATE SET
                display_name=excluded.display_name,
                professional_role=excluded.professional_role,
                experience_years=excluded.experience_years,
                consent=excluded.consent
            """,
            (
                payload["annotator_id"],
                payload.get("display_name", ""),
                payload.get("professional_role", ""),
                payload.get("experience_years", ""),
                1,
                now,
            ),
        )
        con.commit()
    finally:
        con.close()


def answered_task_ids(annotator_id: str, task_type: str) -> set[str]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT task_id FROM annotations WHERE annotator_id=? AND task_type=?",
            (annotator_id, task_type),
        ).fetchall()
    finally:
        con.close()
    return {row[0] for row in rows}


def progress(annotator_id: str, task_type: str, tasks: list[dict[str, Any]]) -> tuple[int, int]:
    total = sum(task["task_type"] == task_type for task in tasks)
    answered = len(answered_task_ids(annotator_id, task_type)) if annotator_id else 0
    return answered, total


def next_task(
    tasks: list[dict[str, Any]],
    annotator_id: str,
    task_type: str,
    skipped: set[str],
) -> dict[str, Any] | None:
    answered = answered_task_ids(annotator_id, task_type)
    for task in tasks:
        if task["task_type"] == task_type and task["task_id"] not in answered and task["task_id"] not in skipped:
            return task
    for task in tasks:
        if task["task_type"] == task_type and task["task_id"] not in answered:
            return task
    return None


def save_annotation(task: dict[str, Any], payload: dict[str, Any]) -> None:
    now = timestamp()
    con = connect()
    try:
        con.execute(
            """
            INSERT INTO annotations
                (task_id, task_type, annotator_id, judgement, span_correct, role_correct,
                 anchor_correct, usefulness, confidence, corrected_span, corrected_role,
                 notes, payload, task_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, annotator_id) DO UPDATE SET
                judgement=excluded.judgement,
                span_correct=excluded.span_correct,
                role_correct=excluded.role_correct,
                anchor_correct=excluded.anchor_correct,
                usefulness=excluded.usefulness,
                confidence=excluded.confidence,
                corrected_span=excluded.corrected_span,
                corrected_role=excluded.corrected_role,
                notes=excluded.notes,
                payload=excluded.payload,
                task_json=excluded.task_json,
                updated_at=excluded.updated_at
            """,
            (
                task["task_id"],
                task["task_type"],
                payload["annotator_id"],
                payload["judgement"],
                payload.get("span_correct", ""),
                payload.get("role_correct", ""),
                payload.get("anchor_correct", ""),
                payload.get("usefulness"),
                payload.get("confidence"),
                payload.get("corrected_span", ""),
                payload.get("corrected_role", ""),
                payload.get("notes", ""),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(task, ensure_ascii=False),
                now,
                now,
            ),
        )
        con.commit()
    finally:
        con.close()


def response_rows() -> list[dict[str, Any]]:
    con = connect()
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM annotations ORDER BY created_at, annotation_id").fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fields = [
        "annotation_id",
        "task_id",
        "task_type",
        "annotator_id",
        "judgement",
        "span_correct",
        "role_correct",
        "anchor_correct",
        "usefulness",
        "confidence",
        "corrected_span",
        "corrected_role",
        "notes",
        "created_at",
        "updated_at",
        "payload",
        "task_json",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def highlight_text(text: str, spans: list[dict[str, Any]]) -> str:
    valid = [
        span
        for span in spans
        if isinstance(span.get("start"), int)
        and isinstance(span.get("end"), int)
        and span["start"] >= 0
        and span["end"] > span["start"]
    ]
    valid.sort(key=lambda span: span["start"])
    if not valid:
        return html.escape(text)
    chunks = []
    cursor = 0
    for span in valid:
        if span["start"] < cursor:
            continue
        chunks.append(html.escape(text[cursor : span["start"]]))
        chunks.append(
            f"<mark title='{html.escape(str(span.get('role', '')))}'>"
            f"{html.escape(text[span['start'] : span['end']])}</mark>"
        )
        cursor = span["end"]
    chunks.append(html.escape(text[cursor:]))
    return "".join(chunks)


def render_task(task: dict[str, Any]) -> None:
    meta = [
        f"任务编号：{task['task_id']}",
        f"数据集：{task.get('dataset', '')}",
        f"划分：{task.get('split', '')}",
        f"系统标签：{task.get('system_label', '')} {ROLE_NAMES.get(task.get('system_label', ''), '')}",
    ]
    if task.get("level"):
        meta.append(f"等级：{task['level']}")
    st.caption(" ｜ ".join(meta))
    st.markdown(
        f"""
        <div class="sentence-box">
          {highlight_text(task.get("text", ""), task.get("candidate_spans", []))}
        </div>
        """,
        unsafe_allow_html=True,
    )
    links = task.get("candidate_links") or []
    spans = task.get("candidate_spans") or []
    if links:
        st.subheader("候选分析")
        for i, link in enumerate(links, 1):
            evidence = "；".join(link.get("evidence") or [])
            with st.container(border=True):
                st.write(f"候选 {i}：**{link.get('span_text', '')}**")
                st.write(f"角色：{link.get('role', '')} {ROLE_NAMES.get(link.get('role', ''), '')}")
                st.write(f"谓词 token：{link.get('predicate_token_id', '无')}")
                if evidence:
                    st.caption(f"证据：{evidence}")
    elif spans:
        st.subheader("候选片段")
        for i, span in enumerate(spans, 1):
            with st.container(border=True):
                st.write(f"候选 {i}：**{span.get('text', '')}**")
                st.caption(f"角色/来源：{span.get('role', '')}")
    else:
        st.info("系统未给出具体时间片段。请判断该句是否确实没有相关时间状语，或是否漏检。")


def is_admin() -> bool:
    token = st.secrets.get("ADMIN_TOKEN", "")
    if not token:
        return True
    supplied = st.sidebar.text_input("管理员口令", type="password")
    return supplied == token


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .sentence-box {
            margin: 0.5rem 0 1.2rem 0;
            padding: 1.1rem 1.25rem;
            border: 1px solid #d7dce3;
            border-radius: 8px;
            background: #fff;
            font-size: 1.35rem;
            line-height: 2.0;
        }
        mark {
            padding: 0.1rem 0.25rem;
            border-radius: 4px;
            color: #0f172a;
            background: #d7f3ed;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    inject_css()
    init_db()
    tasks = load_tasks(str(TASK_PATH))

    if "annotator_id" not in st.session_state:
        st.session_state.annotator_id = ""
    if "task_type" not in st.session_state:
        st.session_state.task_type = "weak_label_audit"
    if "skipped" not in st.session_state:
        st.session_state.skipped = set()

    st.title("时间词语作状语教师评审")
    st.caption("用于弱标签、模型输出和教学案例的匿名教学有效性验证")

    with st.sidebar:
        st.header("评审信息")
        with st.form("annotator_form"):
            annotator_id = st.text_input("标注编号", value=st.session_state.annotator_id, placeholder="如 T01")
            display_name = st.text_input("称呼或姓名缩写", placeholder="可留空")
            professional_role = st.selectbox(
                "身份",
                ["国际中文教师", "国际中文教育研究生", "现代汉语/语言学研究者", "其他"],
            )
            experience_years = st.selectbox("相关经验", ["0-1年", "1-3年", "3-5年", "5年以上"])
            consent = st.checkbox("我知晓本评审仅收集语法判断和教学可用性评分，结果将匿名用于论文统计。")
            started = st.form_submit_button("进入评审")
        if started:
            if not annotator_id.strip():
                st.error("请填写标注编号。")
            elif not consent:
                st.error("请先确认知情说明。")
            else:
                payload = {
                    "annotator_id": annotator_id.strip(),
                    "display_name": display_name.strip(),
                    "professional_role": professional_role,
                    "experience_years": experience_years,
                }
                save_annotator(payload)
                st.session_state.annotator_id = annotator_id.strip()
                st.success("已进入评审。")

        st.divider()
        task_type = st.selectbox(
            "任务类型",
            list(TASK_NAMES.keys()),
            format_func=lambda key: TASK_NAMES[key],
            index=list(TASK_NAMES.keys()).index(st.session_state.task_type),
        )
        if task_type != st.session_state.task_type:
            st.session_state.task_type = task_type
            st.session_state.skipped = set()
            st.rerun()

        answered, total = progress(st.session_state.annotator_id, st.session_state.task_type, tasks)
        st.progress(answered / total if total else 0)
        st.write(f"已完成 {answered} / {total}")

        st.divider()
        st.header("数据导出")
        rows = response_rows()
        st.caption(f"当前已收集 {len(rows)} 条评审记录")
        if is_admin():
            st.download_button(
                "下载 CSV",
                data=csv_bytes(rows),
                file_name="annotation_responses.csv",
                mime="text/csv",
            )
        else:
            st.warning("请输入管理员口令后导出。")

    if not st.session_state.annotator_id:
        st.info("请先在左侧填写评审信息并进入评审。")
        return

    task = next_task(tasks, st.session_state.annotator_id, st.session_state.task_type, st.session_state.skipped)
    if not task:
        st.success("当前任务类型已全部完成。可以切换任务类型继续评审。")
        return

    col_title, col_skip = st.columns([0.8, 0.2])
    with col_title:
        st.subheader(TASK_NAMES[task["task_type"]])
    with col_skip:
        if st.button("跳过此题"):
            st.session_state.skipped.add(task["task_id"])
            st.rerun()

    render_task(task)

    with st.form(f"annotation_{task['task_id']}"):
        st.subheader("评审判断")
        judgement = st.radio(
            "总体判断",
            ["correct", "partial", "incorrect", "not_sure"],
            format_func=lambda value: {
                "correct": "正确",
                "partial": "部分正确",
                "incorrect": "不正确",
                "not_sure": "不确定",
            }[value],
            horizontal=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            span_correct = st.selectbox("时间片段", ["", "yes", "partial", "no"], format_func=choice_name)
        with c2:
            role_correct = st.selectbox("语法角色", ["", "yes", "partial", "no"], format_func=choice_name)
        with c3:
            anchor_correct = st.selectbox(
                "关联谓词",
                ["", "yes", "partial", "no", "not_applicable"],
                format_func=choice_name,
            )
        c4, c5 = st.columns(2)
        with c4:
            usefulness = st.slider("教学可用性", 1, 5, 3)
        with c5:
            confidence = st.slider("判断把握度", 1, 5, 3)
        c6, c7 = st.columns(2)
        with c6:
            corrected_span = st.text_input("修正后的时间片段")
        with c7:
            corrected_role = st.selectbox(
                "修正后的角色",
                ["", "TADV", "TIME_ATTR", "DUR_COMP", "FREQ_ADV", "TIME_NON_ADV", "NONE"],
                format_func=lambda value: f"{value} {ROLE_NAMES.get(value, '')}" if value else "",
            )
        notes = st.text_area("备注", placeholder="可说明为什么不正确，或它是否适合课堂讲解。")
        submitted = st.form_submit_button("提交并进入下一题", type="primary")

    if submitted:
        payload = {
            "annotator_id": st.session_state.annotator_id,
            "task_id": task["task_id"],
            "judgement": judgement,
            "span_correct": span_correct,
            "role_correct": role_correct,
            "anchor_correct": anchor_correct,
            "usefulness": usefulness,
            "confidence": confidence,
            "corrected_span": corrected_span,
            "corrected_role": corrected_role,
            "notes": notes,
        }
        save_annotation(task, payload)
        st.toast("已提交")
        st.rerun()


def choice_name(value: str) -> str:
    return {
        "": "",
        "yes": "正确",
        "partial": "部分正确",
        "no": "不正确",
        "not_applicable": "不适用",
    }[value]


if __name__ == "__main__":
    main()
