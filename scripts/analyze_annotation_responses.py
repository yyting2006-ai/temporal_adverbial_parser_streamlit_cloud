from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any


JUDGEMENT_VALUES = ["correct", "partial", "incorrect", "not_sure"]


def load_responses_from_db(path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM annotations ORDER BY created_at, annotation_id").fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        item = dict(row)
        for key in ["payload", "task_json"]:
            if item.get(key):
                item[key] = json.loads(item[key])
        out.append(item)
    return out


def load_responses_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ["payload", "task_json"]:
            if row.get(key):
                try:
                    row[key] = json.loads(row[key])
                except json.JSONDecodeError:
                    pass
    return rows


def proportion(rows: list[dict[str, Any]], field: str, positives: set[str]) -> float | None:
    values = [row.get(field) for row in rows if row.get(field)]
    if not values:
        return None
    return sum(value in positives for value in values) / len(values)


def pairwise_kappa(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("judgement"):
            by_task[row["task_id"]].append(row)

    pairs = []
    for task_id, task_rows in by_task.items():
        for a, b in combinations(task_rows, 2):
            if a["annotator_id"] == b["annotator_id"]:
                continue
            pairs.append((a["judgement"], b["judgement"]))
    if not pairs:
        return []

    observed = sum(a == b for a, b in pairs) / len(pairs)
    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    total = len(pairs)
    expected = sum((left[v] / total) * (right[v] / total) for v in JUDGEMENT_VALUES)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else None
    return [
        {
            "comparison": "all_pairwise",
            "n_pairs": len(pairs),
            "observed_agreement": observed,
            "expected_agreement": expected,
            "cohen_like_kappa": kappa,
        }
    ]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row.get("task_type", "unknown")].append(row)

    summary: dict[str, Any] = {
        "n_annotations": len(rows),
        "n_annotators": len({row.get("annotator_id") for row in rows if row.get("annotator_id")}),
        "by_task_type": {},
        "agreement": pairwise_kappa(rows),
    }
    for task_type, task_rows in by_type.items():
        usefulness = [
            int(row["usefulness"])
            for row in task_rows
            if str(row.get("usefulness", "")).isdigit()
        ]
        confidence = [
            int(row["confidence"])
            for row in task_rows
            if str(row.get("confidence", "")).isdigit()
        ]
        summary["by_task_type"][task_type] = {
            "n": len(task_rows),
            "judgement_counts": dict(Counter(row.get("judgement") for row in task_rows)),
            "strict_correct_rate": proportion(task_rows, "judgement", {"correct"}),
            "lenient_correct_rate": proportion(task_rows, "judgement", {"correct", "partial"}),
            "span_correct_rate": proportion(task_rows, "span_correct", {"yes"}),
            "role_correct_rate": proportion(task_rows, "role_correct", {"yes"}),
            "anchor_correct_rate": proportion(task_rows, "anchor_correct", {"yes"}),
            "mean_usefulness": mean(usefulness) if usefulness else None,
            "mean_confidence": mean(confidence) if confidence else None,
        }
    return summary


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Human Validation Summary",
        "",
        f"- Total annotations: {summary['n_annotations']}",
        f"- Annotators: {summary['n_annotators']}",
        "",
        "## By Task Type",
        "",
    ]
    for task_type, item in summary["by_task_type"].items():
        lines.extend(
            [
                f"### {task_type}",
                f"- n = {item['n']}",
                f"- judgement counts = {item['judgement_counts']}",
                f"- strict correct rate = {item['strict_correct_rate']}",
                f"- lenient correct rate = {item['lenient_correct_rate']}",
                f"- span correct rate = {item['span_correct_rate']}",
                f"- role correct rate = {item['role_correct_rate']}",
                f"- anchor correct rate = {item['anchor_correct_rate']}",
                f"- mean usefulness = {item['mean_usefulness']}",
                f"- mean confidence = {item['mean_confidence']}",
                "",
            ]
        )
    if summary["agreement"]:
        lines.extend(["## Agreement", ""])
        for item in summary["agreement"]:
            lines.extend(
                [
                    f"- {item['comparison']}: n_pairs={item['n_pairs']}, "
                    f"observed={item['observed_agreement']:.3f}, "
                    f"expected={item['expected_agreement']:.3f}, "
                    f"kappa={item['cohen_like_kappa'] if item['cohen_like_kappa'] is None else round(item['cohen_like_kappa'], 3)}",
                ]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze online annotation responses.")
    parser.add_argument("--db", type=Path, default=Path("human_validation/annotations.sqlite"))
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--output-json", type=Path, default=Path("human_validation/validation_summary.json"))
    parser.add_argument("--output-md", type=Path, default=Path("human_validation/validation_summary.md"))
    args = parser.parse_args()

    rows = load_responses_from_csv(args.csv) if args.csv else load_responses_from_db(args.db)
    summary = summarize(rows)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(args.output_md, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
