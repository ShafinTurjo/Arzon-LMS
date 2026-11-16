# Arzon LMS

A mobile-friendly learning management system for coaching centres and schools, built with Django, server-rendered HTML and SQLite. Real accounts, database-backed workflows and permission checks; no browser-only mock data or public uploads.

## Included

- **Admissions:** student profiles, admission numbers, guardian contacts, course/subject/batch/shift assignment, active status and recorded batch transfers.
- **Routine & attendance:** dated class sessions, assigned teachers, overlap validation, present/late/absent records, class cancellation/rescheduling notifications, guardian absence messages.
- **Learning library:** private PDF/image uploads, notes, question banks, recording/live-class HTTPS links; course and batch access control.
- **Assessments:** timed MCQs with autosave and server-enforced deadlines, weighted auto-marking, written uploads, teacher marking, assignments with deadlines, published results, competition-ranked merit lists and subject progress.
- **Family portal:** student and guardian views of routines, attendance, materials, published exam results, marked assignments, fees and outstanding dues; multiple children per guardian.
- **Staff workspace:** owner/admin management, assigned-batch teacher access, accountant collections and balances; six roles.
- **Communication:** batch notices, 24-hour exam reminders, in-app notifications, student doubts and teacher replies, SMS webhook and browser-push integrations.
- **Reliability:** password validation, DB-backed login throttling, CSRF, role/object checks, private downloads, payment/result change history, daily backup worker, tests and CI.

## Quick start (Python 3.12)

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DEBUG=1
python manage.py migrate
python manage.py create_owner your_username
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in. There are **no default production passwords**. The owner command prompts privately for a password. Application settings read environment variables; `.env` is automatically read by Docker Compose, not by bare Python.

For a separate, empty **local demo** database, run `DEBUG=1 python manage.py seed_demo` instead of `create_owner`. This prints newly generated random passwords and creates fictional student/batch data. Never attach production SMS credentials to that database. Do not commit passwords or demo database files.

## First setup

1. Create teacher, student and guardian **Accounts**. Share each initial password securely; users can change it under Account security.
2. Add a **Course**, its **Subjects**, and **Batches** with teachers and shifts.
3. Admit each **Student**, select the student account, batch and subjects, and link one or more guardian accounts. Link the same guardian to multiple admissions for a family view.
4. Add dated **Class routine** entries. Mark attendance after class begins. Unmarked students are never silently treated as present.
5. Add **Materials**, **Exams**, **Questions**, and **Assignments**. For MCQs, create questions before publishing. Correct options never appear in student responses.
6. Add **Invoices**, then record **Payments** with unique receipt numbers. These are manual collection records, not bKash/Nagad payment-gateway checkout.
7. Run the background worker for reminders, timed-out exams, external deliveries and daily backups.

MCQ question marks are weights, normalized to the exam's total marks. Timing, question contents and total marks lock after the first attempt. The first start establishes a persisted deadline capped by the exam closing time. After expiry only previously saved answers count. Result changes remain visible in history. Students see exam scores only after staff publishes results.

Classes are dated sessions; create each occurrence of a repeating weekly routine. Inactive students cannot start exams or access new learning content. A batch transfer changes future content access; historical results and fees remain attached to the student.

## Role permissions

| Capability | Owner | Admin | Accountant | Teacher | Student | Guardian |
|---|---|---|---|---|---|---|
| Staff accounts | All | Non-admin roles | — | — | — | — |
| Admissions, courses, batches | Yes | Yes | Read students | Assigned students | Own profile | Linked children |
| Routine, library, exams, assignments | All | All | — | Assigned batches | Own batch/course | Children's batches/courses |
| Mark attendance / submissions | All | All | — | Assigned batches | — | — |
| Fees / payments | All | All | All | — | Own | Linked children |
| Submit exam / assignment / doubt | — | — | — | — | Own | — |
| Audit / delivery status | Yes | Yes | — | — | — | — |

A single installation represents one institution. This is not a multi-tenant SaaS. Roles are fixed after account creation to preserve relationships. Admin cannot create or edit owner/admin accounts. No destructive record deletion UI is provided. Django system admin only exposes a read-only audit view to explicitly created superusers; ordinary owners use the application.

## Production deployment

Use a persistent Linux server/VM with Docker, HTTPS and persistent volumes. SQLite is suitable for an initial single-instance installation; move to PostgreSQL and test transaction behavior before horizontally scaling or serving a high-concurrency institution. Do not deploy this SQLite configuration to an ephemeral/serverless filesystem.

```sh
cp .env.example .env
# Set a cryptographically random SECRET_KEY, your real host and HTTPS origin.
# Example generation: python -c 'import secrets; print(secrets.token_urlsafe(64))'
docker compose up --build -d
docker compose exec web python manage.py create_owner your_username
```

Put a trusted HTTPS reverse proxy in front of `127.0.0.1:8000`. Set `TRUST_PROXY=1` only when the proxy strips incoming `X-Forwarded-Proto` and sets it from the actual connection. Production defaults enforce HTTPS and secure cookies. Do not expose `/app/data/private_uploads`; all downloads go through authenticated Django views. Limit upload request bodies to 25 MB at the proxy. Uploaded PDFs/images are validated by extension, size and basic signatures; full malware scanning is not included.

**Compose runs a separate worker**, which checks every minute and takes one backup per day. It shares data with the web app and keeps backups on a separate named volume. Before first run, ensure `/app/backups` is writable by UID 10001 (see Operations). Live provider delivery and production hosting are not activated just by committing this repository.

## SMS integration

Set `SMS_WEBHOOK_URL` to your trusted HTTPS adapter endpoint and `SMS_WEBHOOK_TOKEN` to its secret. The adapter translates this contract to your chosen Bangladeshi SMS provider:

```http
POST /send
Authorization: Bearer <token>
Content-Type: application/json
Idempotency-Key: <stable-message-key>

{"to":"+8801...","message":"...","idempotency_key":"..."}
```

Return `{"accepted": true}` only when the provider accepted the message. Honor the stable idempotency key to avoid duplicate SMS on retry. The worker retries failed deliveries up to five times, at least five minutes apart. “Sent” means accepted by the provider, not carrier-confirmed receipt. Without credentials the outbox remains pending; in-app notifications still work.

## Browser push

Generate a VAPID key pair with your push tooling and set `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` and `VAPID_SUBJECT`. Sign in, open **Notifications**, and choose **Enable browser notifications**. Requires HTTPS (localhost is allowed for development), browser support and explicit browser permission. Invalid/expired subscriptions are removed on provider rejection. Only known browser-push service domains are accepted to prevent arbitrary outbound requests. Browser push is at-least-once; the notification tag reduces duplicate display. No authenticated data is cached by the service worker.

## Operations

```sh
python manage.py worker                  # keep one scheduler running
python manage.py worker --once           # one full cycle; useful for smoke checks
python manage.py run_scheduled           # reminders + expiry + delivery only
python manage.py dispatch_notifications  # delivery only
python manage.py backup --directory /secure/backup/path
python manage.py test
python manage.py check --deploy
```

The Docker image creates `/app/backups` owned by the application user. Docker named volumes inherit this ownership on first creation. If restoring an existing volume, correct its ownership to UID 10001 before starting the worker.

Backups contain a consistent SQLite snapshot and private uploads and retain the newest 14 archives. Copy encrypted archives off-site using your infrastructure backup system; a volume on the same host is not disaster recovery. Do not commit archives to GitHub.

**Restore drill:** stop web and worker; preserve current data separately; inspect the trusted archive with `tar -tzf`; restore `db.sqlite3` and `private_uploads/` into `DATA_DIR`; ensure UID 10001 owns restored files; restart, run `migrate`, then verify a known profile and private download. Archive extraction must only use trusted backups. Backups contain personal information; restrict access. Use an external monitor for worker failures, backup freshness and disk space. Audit rows are append-only through the application, not cryptographically tamper-proof against database administrators.

## Verification and remaining deployment work

The repository includes automated tests for permission boundaries, multiple-child guardian access, timer expiry, score privacy, uploads, deadlines, payment balances, audit history, notification deduplication and login throttling. CI runs migrations, tests, a backup smoke check and Django production security checks.

SMS/push live delivery needs your provider credentials and an end-to-end test. Online payment checkout, video hosting/conferencing, recurring weekly schedule generation, malware scanning, off-site backup transport and password recovery email are not included. Recorded/live classes use external HTTPS links; videos are not hosted by this app. Account recovery is handled by an authorized manager resetting a password. This is an initial implementation: run your institution's acceptance tests with a staging database before importing real student records.

## Stack

Python 3.12, [Django 5.2 LTS](https://www.djangoproject.com/download/), Gunicorn, WhiteNoise, SQLite, pywebpush. Minimal JavaScript; ordinary navigation and forms work without it. Exam autosave, live countdown and browser push need JavaScript. No external fonts or front-end CDN dependencies.
