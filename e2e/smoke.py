"""Browser smoke test of the running dev app (docker compose run --rm e2e).

Works on any non-empty dataset. Every change it makes (measurement, "sewn" flag) is reverted.
Exits non-zero on the first failed check. Screenshots go to e2e/output/.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = os.environ.get("BASE_URL", "http://web:8000")
OUT = Path(__file__).parent / "output"
# Chromium warns about COOP on plain-HTTP non-localhost origins: not an app error.
IGNORED_CONSOLE = ("Cross-Origin-Opener-Policy",)

errors: list[str] = []
failures: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label} {detail}")
    if not ok:
        failures.append(label)


def count(page: Page) -> int:
    return int(page.inner_text("#count"))


def login(page: Page) -> None:
    page.goto(f"{BASE}/")
    page.fill("#id_username", os.environ["E2E_USER"])
    page.fill("#id_password", os.environ["E2E_PASSWORD"])
    page.click("button[type=submit]")
    page.wait_for_selector(".card")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console",
            lambda m: (
                m.type == "error"
                and not any(s in m.text for s in IGNORED_CONSOLE)
                and errors.append(m.text)
            ),
        )

        login(page)
        total = count(page)
        check("catalogue lists patterns", total > 0, total)
        page.screenshot(path=OUT / "catalogue.png")

        first_name = page.locator(".card b").first.inner_text()
        page.fill("#q", first_name)
        page.wait_for_timeout(300)
        check("search narrows the list", 0 < count(page) <= total, count(page))
        page.click("text=Réinitialiser les filtres")
        page.wait_for_timeout(300)
        check("reset restores the list", count(page) == total)

        family = page.locator("aside select").nth(2)  # profile, brand, family
        family.select_option(index=1)
        page.wait_for_timeout(300)
        check("family facet filters", count(page) <= total, count(page))
        page.click("text=Réinitialiser les filtres")

        # Measurement profile: set a chest measure, check persistence, restore it.
        chest = page.locator(".meas input").first
        original = chest.input_value()
        chest.fill("88")
        page.wait_for_timeout(1500)
        page.reload()
        page.wait_for_selector(".card")
        page.wait_for_timeout(500)
        check(
            "measurement persisted",
            page.locator(".meas input").first.input_value() in ("88", "88.0"),
        )
        page.locator(".meas input").first.fill(original)
        page.wait_for_timeout(1500)

        # Pattern sheet: open, toggle "sewn" twice, close.
        page.locator(".card").first.click()
        page.wait_for_selector("#modal h2")
        check("sheet opens", page.locator("#modal h2").is_visible())
        page.screenshot(path=OUT / "sheet.png")
        sewn = page.locator("#modal .sheet-actions input[type=checkbox]")
        before = sewn.is_checked()
        sewn.click()
        page.wait_for_timeout(800)
        check("sewn flag saved", page.inner_text("#modal .sheet-actions .disc") == "Enregistré")
        sewn.click()
        page.wait_for_timeout(800)
        check("sewn flag restored", sewn.is_checked() == before)

        files = page.locator("#modal .files a")
        if files.count():
            response = page.request.get(BASE + files.first.get_attribute("href"))
            check(
                "pattern file downloads",
                response.status == 200,
                response.headers.get("content-type"),
            )

        page.click("text=Modifier la fiche")
        page.wait_for_selector("form.page")
        check("edit page renders", "Modifier" in page.inner_text("form.page h2"))
        page.go_back()
        page.wait_for_selector(".card")
        browser.close()

    check("no JavaScript errors", not errors, errors)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
