from __future__ import annotations

import csv
import html
import io
import json
import os
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
    "model_output_review": "模型输出判断（核对系统预测）",
    "teaching_case_review": "教学案例可用性",
}

TASK_DESCRIPTIONS = {
    "weak_label_audit": "请判断弱监督标签是否符合该时间表达在中文教学中的语法功能。主要看系统标签与候选片段是否合理。",
    "model_output_review": "请把页面中的候选结果当作模型预测来核对：它是否找对时间片段、是否判成正确语法角色、是否关联到合适的谓词/事件。不是让您重新做整句标注。",
    "teaching_case_review": "请判断该句是否适合作为时间词语作状语的教学诊断或课堂讲解案例。",
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

ANNOTATOR_IDS = ["请选择标注编号"] + [f"T{i:02d}" for i in range(1, 21)]

JUDGEMENT_NAMES = {
    "correct": "正确",
    "partial": "部分正确",
    "incorrect": "不正确",
    "not_sure": "不确定",
}

METRIC_EXPLANATIONS = {
    "overall": "总体判断：请综合判断这一条系统结果是否可用于论文统计。若片段、角色和关联谓词基本都对，选“正确”；若只对了一部分，选“部分正确”。",
    "span": "时间片段：系统高亮或列出的时间表达范围是否准确，例如“昨天晚上”“三年后”“2012年12月”。多字、少字、漏掉核心时间词，都不算完全正确。",
    "role": "语法角色：系统给出的类别是否准确。重点区分 TADV（时间词语作状语）、TIME_ATTR（时间定语）、DUR_COMP（时量/补语）、FREQ_ADV（频率副词）、TIME_NON_ADV（有时间词但不是状语）。",
    "anchor": "关联谓词：时间表达修饰或限定的动作/事件是否找对。例如“昨天我去了北京”中，“昨天”关联“去”。若本题没有显示谓词或不适合判断，可选“不适用”。",
    "usefulness": "教学可用性：这条结果对课堂讲解、错句诊断、例句筛选是否有帮助。它不等同于模型准确率，而是教学场景下是否值得使用。",
    "confidence": "判断把握度：您对自己这道题判断的信心。句子含混、语境不足或边界难分时，可以选择较低把握度。",
}


st.set_page_config(
    page_title="时间词语作状语教师评审",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_secret(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@st.cache_resource(show_spinner=False)
def supabase_client():
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_SERVICE_ROLE_KEY") or get_secret("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
    except Exception as exc:
        st.warning(f"Supabase 依赖未安装，已临时使用 SQLite。错误：{exc}")
        return None
    return create_client(url, key)


def using_supabase() -> bool:
    return supabase_client() is not None


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
    if using_supabase():
        return
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
    client = supabase_client()
    if client:
        client.table("annotators").upsert(
            {
                "annotator_id": payload["annotator_id"],
                "display_name": payload.get("display_name", ""),
                "professional_role": payload.get("professional_role", ""),
                "experience_years": payload.get("experience_years", ""),
                "consent": True,
                "created_at": now,
            },
            on_conflict="annotator_id",
        ).execute()
        return

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
    client = supabase_client()
    if client:
        result = (
            client.table("annotations")
            .select("task_id")
            .eq("annotator_id", annotator_id)
            .eq("task_type", task_type)
            .execute()
        )
        return {row["task_id"] for row in result.data or []}

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
    row = {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "annotator_id": payload["annotator_id"],
        "judgement": payload["judgement"],
        "span_correct": payload.get("span_correct", ""),
        "role_correct": payload.get("role_correct", ""),
        "anchor_correct": payload.get("anchor_correct", ""),
        "usefulness": payload.get("usefulness"),
        "confidence": payload.get("confidence"),
        "corrected_span": payload.get("corrected_span", ""),
        "corrected_role": payload.get("corrected_role", ""),
        "notes": payload.get("notes", ""),
        "payload": payload,
        "task_json": task,
        "created_at": now,
        "updated_at": now,
    }
    client = supabase_client()
    if client:
        client.table("annotations").upsert(row, on_conflict="task_id,annotator_id").execute()
        return

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
    client = supabase_client()
    if client:
        result = client.table("annotations").select("*").order("created_at").execute()
        return result.data or []

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
    writer.writerows({field: csv_cell(row.get(field, "")) for field in fields} for row in rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def csv_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


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


def candidate_html(task: dict[str, Any]) -> str:
    links = task.get("candidate_links") or []
    spans = task.get("candidate_spans") or []
    if not links and not spans:
        return "<div class='candidate-mini'>无候选片段：请判断是否确实没有相关时间状语，或是否漏检。</div>"
    cards = []
    if links:
        for i, link in enumerate(links[:4], 1):
            role = html.escape(str(link.get("role", "")))
            role_name = html.escape(ROLE_NAMES.get(link.get("role", ""), ""))
            span = html.escape(str(link.get("span_text", "")))
            pred = html.escape(str(link.get("predicate_token_id", "无")))
            cards.append(
                "<div class='candidate-mini'>"
                f"<b>候选{i}</b>：{span}<br>"
                f"角色：{role} {role_name}<br>"
                f"谓词 token：{pred}"
                "</div>"
            )
        if len(links) > 4:
            cards.append(f"<div class='candidate-mini'>另有 {len(links) - 4} 个候选，已省略细节。</div>")
    else:
        for i, span in enumerate(spans[:4], 1):
            text = html.escape(str(span.get("text", "")))
            role = html.escape(str(span.get("role", "")))
            cards.append(f"<div class='candidate-mini'><b>候选{i}</b>：{text}<br>来源/角色：{role}</div>")
        if len(spans) > 4:
            cards.append(f"<div class='candidate-mini'>另有 {len(spans) - 4} 个候选，已省略细节。</div>")
    return "".join(cards)


def render_task_compact(task: dict[str, Any]) -> None:
    meta = [
        f"{task['task_id']}",
        f"{task.get('dataset', '')}",
        f"{task.get('split', '')}",
        f"{task.get('system_label', '')} {ROLE_NAMES.get(task.get('system_label', ''), '')}",
    ]
    if task.get("level"):
        meta.append(f"等级：{task['level']}")
    st.caption(" ｜ ".join(item for item in meta if item))
    st.markdown(
        f"""
        <div class="sentence-box">
          {highlight_text(task.get("text", ""), task.get("candidate_spans", []))}
        </div>
        <div class="candidate-grid">
          {candidate_html(task)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def is_admin() -> bool:
    token = get_secret("ADMIN_TOKEN")
    if not token:
        return True
    supplied = st.sidebar.text_input("管理员口令", type="password")
    return supplied == token


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1400px;
            padding-top: 0.6rem;
            padding-bottom: 0.6rem;
        }
        div[data-testid="stVerticalBlock"] {
            gap: 0.45rem;
        }
        div[data-testid="stForm"] {
            border: 1px solid #d7dce3;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
            background: #ffffff;
        }
        .sentence-box {
            margin: 0.2rem 0 0.55rem 0;
            padding: 0.75rem 0.9rem;
            border: 1px solid #d7dce3;
            border-radius: 8px;
            background: #fff;
            font-size: 1.1rem;
            line-height: 1.65;
            max-height: 14rem;
            overflow: hidden;
        }
        .candidate-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.45rem;
        }
        .candidate-mini {
            border: 1px solid #d7dce3;
            border-radius: 8px;
            background: #fbfcfd;
            padding: 0.5rem 0.65rem;
            font-size: 0.9rem;
            line-height: 1.45;
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

    st.markdown("### 时间词语作状语教师评审")

    with st.sidebar:
        st.header("评审信息")
        with st.form("annotator_form"):
            current_index = (
                ANNOTATOR_IDS.index(st.session_state.annotator_id)
                if st.session_state.annotator_id in ANNOTATOR_IDS
                else 0
            )
            annotator_id = st.selectbox(
                "标注编号",
                ANNOTATOR_IDS,
                index=current_index,
                help="请选择研究者提前分配给您的匿名编号，例如 T01、T02。不要填写真实姓名。",
            )
            display_name = st.text_input("称呼或姓名缩写", placeholder="可留空")
            professional_role = st.selectbox(
                "身份",
                ["国际中文教师", "国际中文教育研究生", "现代汉语/语言学研究者", "其他"],
            )
            experience_years = st.selectbox("相关经验", ["0-1年", "1-3年", "3-5年", "5年以上"])
            consent = st.checkbox("我知晓本评审仅收集语法判断和教学可用性评分，结果将匿名用于论文统计。")
            started = st.form_submit_button("进入评审")
        if started:
            if annotator_id == ANNOTATOR_IDS[0]:
                st.error("请选择标注编号。")
            elif not consent:
                st.error("请先确认知情说明。")
            else:
                payload = {
                    "annotator_id": annotator_id,
                    "display_name": display_name.strip(),
                    "professional_role": professional_role,
                    "experience_years": experience_years,
                }
                save_annotator(payload)
                st.session_state.annotator_id = annotator_id
                st.success("已进入评审。")

        st.divider()
        task_type = st.selectbox(
            "任务类型",
            list(TASK_NAMES.keys()),
            format_func=lambda key: TASK_NAMES[key],
            index=list(TASK_NAMES.keys()).index(st.session_state.task_type),
            help="建议先完成“弱标签审核”和“模型输出判断”。其中“模型输出判断”是核对系统预测，不需要从零标完整句子。",
        )
        st.caption(TASK_DESCRIPTIONS[task_type])
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
        backend = "Supabase" if using_supabase() else "SQLite"
        st.caption(f"当前已收集 {len(rows)} 条评审记录；存储后端：{backend}")
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

    col_title, col_skip = st.columns([0.82, 0.18])
    with col_title:
        st.markdown(f"#### {TASK_NAMES[task['task_type']]}")
        st.caption(TASK_DESCRIPTIONS[task["task_type"]])
    with col_skip:
        if st.button("跳过此题"):
            st.session_state.skipped.add(task["task_id"])
            st.rerun()

    left, right = st.columns([0.58, 0.42], gap="medium")
    with left:
        render_task_compact(task)

    with right, st.form(f"annotation_{task['task_id']}"):
        st.markdown("##### 评审判断")
        st.caption(
            "指标速记：总体=整条结果；片段=高亮范围；角色=语法类别；"
            "谓词=修饰的动作/事件；可用性=教学价值；把握度=您对判断的信心。"
        )
        judgement = st.radio(
            "总体判断",
            ["correct", "partial", "incorrect", "not_sure"],
            format_func=lambda value: JUDGEMENT_NAMES[value],
            horizontal=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            span_correct = st.selectbox(
                "片段",
                ["", "yes", "partial", "no"],
                format_func=choice_name,
                help=METRIC_EXPLANATIONS["span"],
            )
        with c2:
            role_correct = st.selectbox(
                "角色",
                ["", "yes", "partial", "no"],
                format_func=choice_name,
                help=METRIC_EXPLANATIONS["role"],
            )
        with c3:
            anchor_correct = st.selectbox(
                "谓词",
                ["", "yes", "partial", "no", "not_applicable"],
                format_func=choice_name,
                help=METRIC_EXPLANATIONS["anchor"],
            )
        c4, c5 = st.columns(2)
        with c4:
            usefulness = st.selectbox(
                "教学可用性",
                [1, 2, 3, 4, 5],
                index=2,
                format_func=usefulness_name,
                help=METRIC_EXPLANATIONS["usefulness"],
            )
        with c5:
            confidence = st.selectbox(
                "判断把握度",
                [1, 2, 3, 4, 5],
                index=2,
                format_func=confidence_name,
                help=METRIC_EXPLANATIONS["confidence"],
            )
        c6, c7 = st.columns(2)
        with c6:
            corrected_span = st.text_input(
                "修正后的时间片段",
                placeholder="仅在系统片段不对时填写，如：昨天晚上；若无须修正可留空",
                help="这里不是必填项。只有当候选片段漏字、多字或漏检时，才填写您认为正确的时间词语范围。",
            )
        with c7:
            corrected_role = st.selectbox(
                "修正后的角色",
                ["", "TADV", "TIME_ATTR", "DUR_COMP", "FREQ_ADV", "TIME_NON_ADV", "NONE"],
                format_func=lambda value: f"{value} {ROLE_NAMES.get(value, '')}" if value else "",
            )
        notes = st.text_input("备注", placeholder="可说明为什么不正确；可留空。")
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


def usefulness_name(value: int) -> str:
    return {
        1: "1 完全不可用",
        2: "2 用处较小",
        3: "3 一般可用",
        4: "4 比较有用",
        5: "5 非常适合教学/诊断",
    }[value]


def confidence_name(value: int) -> str:
    return {
        1: "1 没有把握",
        2: "2 把握较低",
        3: "3 一般",
        4: "4 比较有把握",
        5: "5 非常有把握",
    }[value]


if __name__ == "__main__":
    main()
