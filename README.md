# patrons-app

Personal sewing pattern catalogue: search and filters, size recommendation from body
measurements, and pattern ingestion. Successor of the static `moteur-patrons.html` MVP.

Stack: Django, PostgreSQL, S3-compatible storage (optional in dev), Granian (WSGI).

## Development

```bash
cp .env.example .env
docker compose up -d --wait        # PostgreSQL on localhost:5433
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Checks (run before every commit):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## Production image

```bash
docker build -t patrons-app .
```

The image runs Granian in WSGI mode on port 8000 and serves static files with WhiteNoise.
Required env: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, and the `S3_*`
variables for media storage. Health endpoint: `/healthz`.

## Data model

See `apps/catalog/models.py`. Pattern files and inventory CSVs are never stored in this
repository (copyrighted material and personal data).

## Importing the MVP inventory

```bash
uv run python manage.py import_inventory /path/to/Patrons --owner <username> [--dry-run]
```

Reads `inventaire-patrons-complet.csv`, `tableaux-tailles.csv`, `_moteur/taglist.json` and
`_vignettes/`. Safe to re-run: patterns are matched on their CSV row number (`legacy_ref`),
size charts are replaced. Values that cannot be normalized are reported as warnings and
left unspecified, never guessed.
