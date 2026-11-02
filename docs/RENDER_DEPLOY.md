# Deploy OSAS to Render

OSAS is a **Flask + PostgreSQL** app. Render can host both the web service and Postgres from the included Blueprint.

## Architecture

```text
GitHub repo  →  Render Web Service (Gunicorn + Flask)
                      ↓
                Render Postgres (osas_bcp)
```

## 1. Push this project to GitHub

```bash
git add .
git commit -m "Prepare OSAS for Render (PostgreSQL)"
git remote add origin https://github.com/YOUR_USERNAME/OSAS.git
git push -u origin main
```

## 2. Deploy with Blueprint (recommended)

1. Go to [render.com](https://render.com) → **New** → **Blueprint**
2. Connect the GitHub repo
3. Confirm `render.yaml`
4. Apply

That creates:

- Web service `osas-bcp`
- Postgres database `osas-postgres` with `DATABASE_URL` wired automatically

Schema and the software-engineer account are created on first start (`scripts/db_bootstrap.py`).

## 3. Manual web service (if you skip Blueprint)

1. **New** → **PostgreSQL** → note the Internal Database URL  
2. **New** → **Web Service** → this repo  

| Field | Value |
|--------|--------|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python scripts/db_bootstrap.py && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120` |

Environment:

| Key | Value |
|-----|--------|
| `DATABASE_URL` | Render Postgres connection string |
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |
| `SECRET_KEY` | long random string |
| `SESSION_COOKIE_SECURE` | `1` |
| `OTP_ENABLED` | `0` (until SMTP is configured) |
| `PGSSL` | `1` |
| `SEED_USERNAME` | `software.engineer` |
| `SEED_PASSWORD` | `Engineer@OSAS2026` (change after first login) |

## 4. First login

```text
https://YOUR-SERVICE.onrender.com
```

| Username | Password | Role |
|----------|----------|------|
| `software.engineer` | `Engineer@OSAS2026` | Administrator |

Health check: `https://YOUR-SERVICE.onrender.com/api/health`

Change `SEED_PASSWORD` on Render and in User Management after the first successful login. `OTP_ENABLED` stays off until you add SMTP.

## Local PostgreSQL

```bash
createdb osas_bcp
copy .env.example .env
python init_db.py
python app.py
```

## Limitations (free / starter)

- Cold starts on free web instances
- Uploads are ephemeral on the web disk
- OSRM can stay unset (heuristic fallback)
- Render Postgres is a paid add-on on current plans; the Blueprint still provisions it

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build OK, site 500 | Logs → usually `DATABASE_URL` missing or SSL (`PGSSL`) |
| `SECRET_KEY` error | Set `SECRET_KEY` and `FLASK_ENV=production` |
| Login cookie issues | `SESSION_COOKIE_SECURE=1` and HTTPS |
| Relation does not exist | Wait for bootstrap; or run `python scripts/db_bootstrap.py` |
| OTP send failed | Keep `OTP_ENABLED=0` or set SMTP |
