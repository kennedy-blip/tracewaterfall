# TraceWaterfall

Distributed request trace, span-latency, and bottleneck analyzer. Vanilla HTML/CSS/JavaScript frontend with Python 3.12 Vercel Functions and SQLite.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m http.server 8000 --directory public
```

The static preview includes a local fallback analyzer. To exercise the API locally, run the Vercel development server (`vercel dev`) from the repository root.

## Deploy

Import this repository into Vercel. The project uses `public/` for static assets and Python functions in `api/`. No Node build step is required.

## API

- `GET /api/traces` — seeded demo traces and spans
- `POST /api/traces` — add a trace or reset demo data
- `POST /api/analyze` — compute span self-time, critical path, and findings

SQLite storage is in-memory per serverless invocation; demo data is re-seeded on each cold start. For durable multi-instance production data, replace the in-memory store with a managed database.