# Browser smoke test

Runs Playwright (Chromium) against the dev app of `compose.yaml`:

```bash
docker compose up -d --wait
docker compose run --rm e2e        # E2E_USER / E2E_PASSWORD default to admin / admin
```

It needs a user with at least one pattern. Changes it makes are reverted. Screenshots are
written to `e2e/output/` (git-ignored).
