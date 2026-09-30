# Deploy OSAS to Render

OSAS is a **Flask + MySQL** app. Render hosts the web service. You still need a **MySQL database** (Render’s free DB is Postgres, which this project does not use).

## Architecture

```text
GitHub repo  →  Render Web Service (Gunicorn + Flask)
                      ↓
              External MySQL (Aiven / Railway / etc.)
```

## 1. Push this project to GitHub

If you have not already:

```bash
git add .
git commit -m "Prepare OSAS for Render deployment"
git remote add origin https://github.com/YOUR_USERNAME/OSAS.git
git push -u origin main
```

## 2. Create a MySQL database (required)

Pick one:

### Option A — Railway MySQL (simple)

1. Go to [railway.app](https://railway.app) → New → Database → MySQL  
2. Open the MySQL service → **Variables** / **Connect**  
3. Copy: host, port, user, password, database name  

### Option B — Aiven MySQL

1. [aiven.io](https://aiven.io) → MySQL service  
2. Copy connection details (SSL is usually required → set `MYSQL_SSL=1`)

### Load the schema

From your PC (replace values):

```bash
mysql -h YOUR_HOST -P 3306 -u YOUR_USER -p YOUR_DATABASE < schema.sql
```

Then seed demo users (optional):

```bash
# Temporarily point local .env at the remote DB, then:
python init_db.py
```

Or run the same SQL/user inserts manually against the remote DB.

## 3. Create the Render web service

1. Go to [render.com](https://render.com) → **New** → **Web Service**  
2. Connect your GitHub repo  
3. Settings:

| Field | Value |
|--------|--------|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120` |
| Instance | Free (or Starter) |

You can also use the included `render.yaml` via **Blueprint** if you prefer.

## 4. Environment variables on Render

In the service → **Environment**, set:

| Key | Example / notes |
|-----|------------------|
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |
| `SECRET_KEY` | long random string (Render can generate) |
| `SESSION_COOKIE_SECURE` | `1` |
| `SHOW_DEMO_CREDENTIALS` | `0` |
| `MYSQL_HOST` | from your MySQL provider |
| `MYSQL_PORT` | `3306` (or provider port) |
| `MYSQL_USER` | from provider |
| `MYSQL_PASSWORD` | from provider |
| `MYSQL_DATABASE` | `osas_bcp` (or provider DB name) |
| `MYSQL_SSL` | `1` for most cloud MySQL hosts |

Optional:

```text
MYSQL_URL=mysql://user:password@host:3306/osas_bcp
```

(You can use `MYSQL_URL` instead of the separate `MYSQL_*` vars.)

## 5. Deploy

Click **Deploy**. When it finishes, open:

```text
https://YOUR-SERVICE.onrender.com
```

Health check:

```text
https://YOUR-SERVICE.onrender.com/api/health
```

## 6. First login

If you ran `init_db.py` against the remote DB:

- Demo accounts exist for **local/dev** — change passwords before sharing the link  
- Prefer creating a fresh admin and setting `SHOW_DEMO_CREDENTIALS=0`

## Important limitations (free Render)

- **Cold starts:** free services sleep; first request after idle can take ~30–60s  
- **Uploads:** disk is ephemeral — profile photos / attachments can disappear after redeploy  
- **OSRM:** leave unset; the app uses heuristic route fallback  
- **MySQL must be external** — this app is not Postgres

## Local files added for Render

| File | Purpose |
|------|---------|
| `Procfile` | Start command |
| `render.yaml` | Optional Render Blueprint |
| `gunicorn` in `requirements.txt` | Production WSGI server |
| `config.py` | Production env / SSL / `MYSQL_URL` support |
| `ProxyFix` in `app.py` | Correct HTTPS cookies behind Render |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build OK, site 500 | Check Render logs → usually wrong MySQL host/password/SSL |
| `SECRET_KEY` error | Set `SECRET_KEY` and `FLASK_ENV=production` |
| Login cookie issues | Ensure `SESSION_COOKIE_SECURE=1` and site is HTTPS |
| `Unknown database` | Create DB + import `schema.sql` |
| SSL connection error | Toggle `MYSQL_SSL=1` or `0` to match your host |

## Checklist before sharing for defense

- [ ] App opens on the Render URL  
- [ ] `/api/health` returns healthy  
- [ ] Login works  
- [ ] Dashboard / incidents / patrol load  
- [ ] Demo passwords changed or disabled  
- [ ] Strong `SECRET_KEY` set  
