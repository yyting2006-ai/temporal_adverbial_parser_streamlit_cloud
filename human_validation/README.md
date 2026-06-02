# Teacher-Facing Human Validation

This folder is for online teacher review data. The web app is intentionally a
pedagogical validity audit, not an unrestricted corpus annotation campaign.

## Why This Is Needed

Reviewer concerns can be addressed with a small but rigorous human validation
stage:

1. Weak-label validity: Are the silver labels produced by dependency rules and
   temporal rules acceptable as proxy supervision?
2. Model-output validity: Do teachers judge the model's predicted temporal
   adverbial roles as correct and pedagogically useful?
3. Teaching-case validity: Can the 374 teaching cases be used as classroom or
   diagnostic examples of temporal adverbial grammar?

## Recommended Human Work

Use 2-3 raters if possible. Suitable raters include CSL/CFL teachers, graduate
students in International Chinese Education, or linguistics/SLA researchers who
have training in Chinese grammar teaching. If only one teacher is available,
call the study a pedagogical validity audit rather than an expert gold-standard
annotation.

Minimum useful target:

- Weak-label audit: 100 items.
- Model-output review: 100-130 items.
- Teaching-case review: at least 100 items, or all 374 if the workload is
  acceptable.

## Ethics and Privacy

Collect only anonymous reviewer IDs and professional background categories. Do
not collect students' private information. If the study is submitted through an
institution, follow local ethics/IRB requirements before public deployment.

Recommended consent wording is already displayed in the web app:

"I know this review only collects grammar judgements and pedagogical usefulness
ratings, and the anonymized results may be used for manuscript statistics."

## Build Tasks

From the project root:

```bash
python3 scripts/build_annotation_tasks.py
```

This creates:

- `data/annotation_tasks/tasks.jsonl`
- `data/annotation_tasks/task_summary.json`

Default task composition:

- 100 weak-label audit items, stratified by label.
- 130 model-output review items, stratified by predicted label where possible.
- 374 teaching-case review items.

## Run the Website Locally

```bash
python3 web_annotator/app.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

Annotations are saved to:

```text
human_validation/annotations.sqlite
```

## Run on a Server or LAN

For a lab intranet or temporary server:

```bash
python3 web_annotator/app.py --host 0.0.0.0 --port 8765 --admin-token CHANGE_ME
```

Then share the server address with teachers. Keep the token private; it protects
CSV export.

## Export Responses

If no admin token is used:

```text
http://127.0.0.1:8765/api/export.csv
```

If an admin token is used:

```text
http://SERVER_IP:8765/api/export.csv?token=CHANGE_ME
```

## Analyze Responses

```bash
python3 scripts/analyze_annotation_responses.py
```

This creates:

- `human_validation/validation_summary.json`
- `human_validation/validation_summary.md`

Reportable metrics include:

- strict correctness rate: Correct / total.
- lenient correctness rate: (Correct + Partly correct) / total.
- span correctness rate.
- role correctness rate.
- anchor correctness rate.
- mean pedagogical usefulness score.
- pairwise agreement and Cohen-like kappa when two or more raters judge the
  same items.

## Suggested Manuscript Wording

We conducted a teacher-facing pedagogical validity audit rather than treating
silver labels as a gold standard. Raters reviewed three types of cases: weak
labels used for proxy supervision, model predictions, and teaching examples.
For each case, raters judged overall correctness, span correctness, role
correctness, predicate-anchor correctness, pedagogical usefulness, and their
own confidence. We report strict correctness, lenient correctness, and
inter-rater agreement where overlapping ratings are available.

## What Not To Claim

Do not claim that the online review produces a universal gold-standard corpus
unless qualified experts independently annotate the same items under a
pre-registered guideline and reach acceptable agreement. The safer and more
accurate claim is that the study provides pedagogical validity evidence for
weak supervision and teacher-facing model output.
