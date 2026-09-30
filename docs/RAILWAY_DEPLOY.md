# Deploy OSAS on Railway (recommended)

Railway can run **both** the Flask app and **MySQL** in one project — simpler than Render for this codebase.

## Architecture

```text
GitHub → Railway Project
            ├── Web service (Gunicorn + Flask)
            └── MySQL database
```

## 1. Push the project to GitHub

```bash
git add .
git commit -m "Prepare OSAS for Railway deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/OSAS.git
git push -u origin main
```

(Skip `remote add` if origin already exists — just `git push`.)

## 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and sign in with GitHub  
2. **New Project** → **Deploy from GitHub repo** → select **OSAS**  
3. Railway will detect Python and deploy the web service  

## 3. Add MySQL

1. In the same project: **+ New** → **Database** → **MySQL**  
2. Wait until MySQL is healthy  

## 4. Connect the web service to MySQL

Open the **web service** → **Variables** → **Add Variable** / **Variable Reference**:

Easiest: reference the MySQL service variables.

| App variable | Source (MySQL service) |
|--------------|-------------------------|
| `MYSQLHOST` | `${{MySQL.MYSQLHOST}}` |
| `MYSQLPORT` | `${{MySQL.MYSQLPORT}}` |
| `MYSQLUSER` | `${{MySQL.MYSQLUSER}}` |
| `MYSQLPASSWORD` | `${{MySQL.MYSQLPASSWORD}}` |
| `MYSQLDATABASE` | `${{MySQL.MYSQLDATABASE}}` |

Or set one URL if Railway shows `MYSQL_URL` / `DATABASE_URL`:

```text
MYSQL_URL=${{MySQL.MYSQL_URL}}
```

Also set these on the **web** service:

| Key | Value |
|-----|--------|
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |
| `SECRET_KEY` | click Generate / paste a long random string |
| `SESSION_COOKIE_SECURE` | `1` |
| `SHOW_DEMO_CREDENTIALS` | `0` |
| `MYSQL_SSL` | `0` (usually fine for private Railway MySQL) |

Start command (if Railway asks / under Settings):

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

(`Procfile` already defines this.)

## 5. Import the database schema

### Option A — Railway CLI + local mysql client

1. Install [Railway CLI](https://docs.railway.app/guides/cli)  
2. From the project folder:

```bash
railway link
railway connect MySQL
```

Then in the MySQL shell, or:

```bash
# After linking, use public TCP proxy / connection URL from MySQL → Connect
mysql -h <PUBLIC_HOST> -P <PORT> -u <USER> -p <DATABASE> < schema.sql
```

### Option B — Temporary local `.env` pointing at Railway

1. MySQL service → **Connect** → copy **public** host/port/user/password/database  
2. Put them in a local `.env`  
3. Run:

```bash
mysql -h HOST -P PORT -u USER -p DATABASE < schema.sql
python init_db.py
```

That creates tables + demo users (`admin` / `security.head`, password `password123`).

**Change those passwords** after first login (or create a new admin and disable demos).

## 6. Generate a public URL

Web service → **Settings** → **Networking** → **Generate Domain**

Open:

```text
https://YOUR-APP.up.railway.app
```

Health check:

```text
https://YOUR-APP.up.railway.app/api/health
```

## 7. Redeploy after env changes

After saving variables, trigger a redeploy (Deployments → Redeploy) so the app picks them up.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| App boots but DB errors | Confirm MySQL vars are on the **web** service, not only on MySQL |
| `Unknown database` | Import `schema.sql` first |
| Build fails on gunicorn | Ensure `requirements.txt` includes `gunicorn==23.0.0` and was pushed |
| Login cookie problems | `FLASK_ENV=production` + `SESSION_COOKIE_SECURE=1` + HTTPS domain |
| Sleep / cold start | Free/hobby plans may sleep; first hit can be slow |

## Defense checklist

- [ ] Public URL opens  
- [ ] `/api/health` OK  
- [ ] Login works  
- [ ] Dashboard / incidents / patrol load  
- [ ] Demo password changed or new admin created  
- [ ] Strong `SECRET_KEY` set  

## Note on uploads

Railway disk can be ephemeral depending on plan. Profile photos / attachments may not persist forever on free tiers — fine for demos; use object storage later if needed.
