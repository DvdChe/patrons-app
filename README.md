# patrons-app

Personal sewing pattern catalogue: search and filters, size recommendation from body
measurements, and pattern ingestion. Successor of the static `moteur-patrons.html` MVP.

Stack: Django, PostgreSQL, S3-compatible storage (optional in dev), Granian (WSGI).

## Development

Everything runs in docker compose (dev image, source bind-mounted, auto-reload):

```bash
docker compose up -d --wait                     # http://127.0.0.1:8000 (migrations applied)
docker compose exec web python manage.py createsuperuser
```

Checks (run before every commit):

```bash
docker compose exec web ruff check . && docker compose exec web ruff format --check .
docker compose exec web pytest
docker compose run --rm e2e                     # browser smoke test, see e2e/README.md
```

The MVP `Patrons` folder is mounted read-only at `/patrons` (default `../../Patrons`,
override with `PATRONS_DIR`).

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

## Adding a pattern

"＋ Ajouter un patron" in the catalogue: upload the downloaded zip or the PDFs/images, the
booklet is read (embedded text, OCR for scanned pages) and a review form is pre-filled.
Nothing is created before the review is validated. Extraction accuracy is measured against
the MVP inventory with:

```bash
docker compose exec web python manage.py evaluate_extraction /patrons --owner <username> --sample 100
```

## Importing the MVP inventory

```bash
docker compose exec web python manage.py import_inventory /patrons --owner <username> [--dry-run]
```

Reads `inventaire-patrons-complet.csv`, `tableaux-tailles.csv`, `_moteur/taglist.json` and
`_vignettes/`. Safe to re-run: patterns are matched on their CSV row number (`legacy_ref`),
size charts are replaced. Values that cannot be normalized are reported as warnings and
left unspecified, never guessed.

Then upload the pattern files (PDF, images, zip contents) to the media storage:

```bash
docker compose exec web python manage.py import_files /patrons --owner <username> --dry-run
docker compose exec web python manage.py import_files /patrons --owner <username> [--ref 12 ...]
```

Files are stored by content hash (identical files stored once, re-runs upload nothing new).
When several patterns share a folder (magazines, books), a file is linked to the pattern whose
name appears in the file name; unmatched files are reported. The full library is about 7 GiB:
in development, prefer `--ref` on a few patterns.
