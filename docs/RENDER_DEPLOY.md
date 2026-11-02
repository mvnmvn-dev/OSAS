Deploy OSAS to Render

OSAS is a Flask + PostgreSQL app. This project uses Render for the web service and Neon for PostgreSQL.

Architecture

Plain Text


GitHub repo  →  Render Web Service (Gunicorn + Flask)
                      ↓
                    Neon



1. Push this project to GitHub

Bash


git add .
git commit -m "Prepare OSAS for Render (PostgreSQL)"
git remote add origin https://github.com/YOUR_USERNAME/OSAS.git
git push -u origin main



2. Deploy with Blueprint (recommended)

1.
Go to 

render.com → New → Blueprint

2.
Connect the GitHub repo

3.
Confirm render.yaml

4.
Apply

That creates the web service. The Blueprint intentionally leaves DATABASE_URL as a secret input so it can keep using your existing Neon database:

•
Web service osas-bcp

•
DATABASE_URL (set to the Neon connection string)

Schema and the software-engineer account are created on first start (scripts/db_bootstrap.py).

3. Manual web service (if you skip Blueprint)

1.
New → PostgreSQL → note the Internal Database URL

2.
New → Web Service → this repo

Field
Value
Runtime
Python 3
Build Command
pip install -r requirements.txt
Start Command
python scripts/db_bootstrap.py && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120




Environment:

Key
Value
DATABASE_URL
Neon PostgreSQL connection string (keep the existing Neon URL)
FLASK_ENV
production
FLASK_DEBUG
0
SECRET_KEY
long random string
SESSION_COOKIE_SECURE
1
OTP_ENABLED
1
SMTP_HOST
smtp.gmail.com
SMTP_PORT
587
SMTP_USER
inigomerv@gmail.com
SMTP_FROM
inigomerv@gmail.com
SMTP_PASSWORD
A Google App Password (set as a Render secret; do not use the Gmail sign-in password)
PGSSL
1
SEED_USERNAME
software.engineer
SEED_EMAIL
jmagnoinigo20@gmail.com (shared by both admin accounts)
SEED_PASSWORD
Engineer@OSAS2026 (change after first login)




4. First login

Plain Text


https://YOUR-SERVICE.onrender.com



Username
Password
Role
software.engineer
Engineer@OSAS2026
Administrator




Health check: https://YOUR-SERVICE.onrender.com/api/health

Create a Google App Password for inigomerv@gmail.com (Google Account → Security → 2-Step Verification → App passwords), then set it as the secret SMTP_PASSWORD in Render. Do not paste the normal Gmail password into Render or the repository. Keep OTP_ENABLED=1 only after all SMTP variables are set. Both existing administrator accounts (admin and software.engineer) use jmagnoinigo20@gmail.com for OTP delivery. Change SEED_PASSWORD on Render and in User Management after the first successful login.

Local PostgreSQL

Bash


createdb osas_bcp
copy .env.example .env
python init_db.py
python app.py



Limitations (free / starter)

•
Cold starts on free web instances

•
Uploads are ephemeral on the web disk

•
OSRM can stay unset (heuristic fallback)

•
Neon database must remain reachable from the Render web service; do not replace its DATABASE_URL with a Render Postgres URL

Troubleshooting

Problem
Fix
Build OK, site 500
Logs → usually DATABASE_URL missing or SSL (PGSSL)
SECRET_KEY error
Set SECRET_KEY and FLASK_ENV=production
Login cookie issues
SESSION_COOKIE_SECURE=1 and HTTPS
Relation does not exist
Wait for bootstrap; or run python scripts/db_bootstrap.py
OTP send failed
Keep OTP_ENABLED=0 or set SMTP


