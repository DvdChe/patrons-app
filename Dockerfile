# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY . .
RUN DJANGO_SECRET_KEY=build DATABASE_URL=postgres://build@localhost/build \
    .venv/bin/python manage.py collectstatic --noinput

FROM python:3.13-slim
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# WSGI: the Django ORM is synchronous, ASGI would only add sync/async overhead.
CMD ["granian", "--interface", "wsgi", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--blocking-threads", "4", "config.wsgi:application"]
