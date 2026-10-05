# UI Design Auditor (full stack)

Python backend (Flask + SQLite + BeautifulSoup/tinycss2) with a browser UI.

## Run
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
pytest -q                # run tests
docker build -t ui-auditor . && docker run -p 8000:8000 -v $PWD/data:/app/data ui-auditor
```

## Two audit engines
- **Browser engine** ("Run audit"): measures the rendered preview, so it also checks touch-target size and viewport overflow, and supports click-to-highlight.
- **Python engine** ("Audit on server" / "Audit URL"): static HTML + CSS cascade (specificity, `!important`, inheritance). Stores every report in SQLite. Gradients and images as backgrounds are skipped for contrast.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/audit` | body `{"html": "..."}` or `{"url": "https://..."}`; returns and saves a report |
| GET | `/api/reports` | list saved reports |
| GET | `/api/reports/<id>` | one full report |
| GET | `/api/reports/<id>/export` | download as JSON |
| DELETE | `/api/reports/<id>` | delete |
| GET | `/api/health` | health check |

URL fetching blocks private, loopback and link-local addresses (re-checked on every redirect), caps pages at 2 MB, and inlines up to 5 stylesheets.

## Layout
```
app.py              Flask routes, safe URL fetch
auditor/engine.py   cascade resolver + audit checks + scoring
auditor/db.py       SQLite storage
static/index.html   UI
tests/              pytest suite
```
