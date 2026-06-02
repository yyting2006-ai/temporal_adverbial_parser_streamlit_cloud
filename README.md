# Streamlit Cloud Deployment

This project includes a Streamlit version of the teacher annotation website.

## Files Needed by Streamlit Cloud

- `streamlit_app.py`: app entrypoint.
- `requirements.txt`: Python dependencies.
- `.streamlit/config.toml`: visual configuration.
- `data/annotation_tasks/tasks.jsonl`: annotation tasks.
- `human_validation/streamlit_annotations.sqlite`: created automatically at runtime.

## Recommended Storage

For formal teacher data collection, use Supabase. Streamlit stores secrets
outside GitHub, and the app now automatically uses Supabase when these secrets
are configured:

```toml
SUPABASE_URL = "https://your-project-ref.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"
ADMIN_TOKEN = "your-private-export-token"
```

Before deployment, create the database tables by running:

```text
supabase_schema.sql
```

in Supabase Dashboard -> SQL Editor.

## SQLite Fallback Warning

Streamlit Community Cloud is convenient for pilot teacher review, but local
SQLite files on the app container are not a durable research database. Export
CSV frequently, especially before redeploying or changing the app. For formal
large-scale data collection, use Supabase or another institution-approved
backend.

## Local Test

```bash
cd "/Volumes/Extreme SSD/VScode/temporal_adverbial_parser_streamlit_cloud"
streamlit run streamlit_app.py
```

If Streamlit is not installed locally:

```bash
python3 -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy on Streamlit Community Cloud

1. Put this project folder into a GitHub repository.
2. Make sure the repository contains `streamlit_app.py`, `requirements.txt`,
   `.streamlit/config.toml`, and `data/annotation_tasks/tasks.jsonl`.
3. Open `https://share.streamlit.io`.
4. Click `Create app`.
5. Choose the GitHub repository and branch.
6. Set the entrypoint file to:

```text
streamlit_app.py
```

7. Optional: in `Advanced settings`, set a Python version such as `3.12`.
8. Recommended: in `Secrets`, paste:

```toml
ADMIN_TOKEN = "your-private-export-token"
SUPABASE_URL = "https://your-project-ref.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"
```

9. Click `Deploy`.

## Share With Teachers

Give teachers the deployed `streamlit.app` URL. Ask them to use anonymous IDs
such as `T01`, `T02`, and `T03`; do not ask them to enter private personal
information.

## Export Data

Open the sidebar. If `ADMIN_TOKEN` is configured, enter it first. Then click
`下载 CSV`.

## Analyze Exported Data

After downloading `annotation_responses.csv`, copy it into the project and run:

```bash
python3 scripts/analyze_annotation_responses.py \
  --csv annotation_responses.csv \
  --output-json human_validation/validation_summary.json \
  --output-md human_validation/validation_summary.md
```

## What to Report in the Paper

Report this as a teacher-facing pedagogical validity audit:

- number and background of raters;
- number of weak-label, model-output, and teaching-case items reviewed;
- strict and lenient correctness rates;
- span, role, and predicate-anchor correctness;
- mean pedagogical usefulness;
- inter-rater agreement if overlapping ratings are collected.
