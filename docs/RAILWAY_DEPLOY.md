# Deploy OSAS on Railway

Railway can run **both** the Flask app and **PostgreSQL** in one project.

## Architecture

```text
GitHub → Railway Project
            ├── Web service (Gunicorn + Flask)
            └── PostgreSQL
```

## 1. Push the project to GitHub

```bash
git add .
git commit -m "Prepare OSAS for Railway deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/OSAS.git
git push -u origin main
```

## 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and sign in with GitHub  
2. **New Project** → **Deploy from GitHub repo** → select **OSAS**

## 3. Add PostgreSQL

1. In the same project: **+ New** → **Database** → **PostgreSQL**  
2. Wait until Postgres is healthy  

## 4. Connect the web service

On the **web** service → **Variables**:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Also set:

| Key | Value |
|-----|--------|
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |
| `SECRET_KEY` | generate a long random string |
| `SESSION_COOKIE_SECURE` | `1` |
| `OTP_ENABLED` | `0` |
| `PGSSL` | `0` (private Railway network) |
| `SEED_USERNAME` | `software.engineer` |
| `SEED_PASSWORD` | `Engineer@OSAS2026` |

Start command (`Procfile` already has this):

```bash
python scripts/db_bootstrap.py && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

Bootstrap creates tables and the software-engineer account on first deploy.

## 5. Public URL

Web service → **Settings** → **Networking** → **Generate Domain**

Login: `software.engineer` / `Engineer@OSAS2026`

Health: `https://YOUR-APP.up.railway.app/api/health`
