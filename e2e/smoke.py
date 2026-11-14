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


def add_pattern(page: Page) -> None:
    """Upload a generated booklet, review it, create the pattern, then delete it."""
    from tests.pdfs import make_pdf

    booklet = make_pdf(
        [
            ["Robe E2E Test", "Taille / Size : 36-44", "Niveau : débutant",
             "Les marges de couture de 1 cm sont incluses."],
            ["Montage"],
        ]
    )  # fmt: skip
    page.click("text=Ajouter un patron")
    page.set_input_files(
        "input[type=file]",
        files=[{"name": "Livret E2E Test.pdf", "mimeType": "application/pdf", "buffer": booklet}],
    )
    page.click("button.primary")
    page.wait_for_selector("text=vérifiez la fiche", timeout=60_000)
    check("size range pre-filled", page.input_value("#id_size_range") == "36 – 44")
    check("seam allowance pre-filled", page.input_value("#id_seam_allowance") == "included")
    page.fill("#id_name", "E2E Test")
    if not page.input_value("#id_brand"):
        page.fill("#id_new_brand", "Marque E2E")
    page.locator(".cands img").first.click()
    page.wait_for_function("document.querySelector('.cropper img')?.complete")
    box = page.locator(".cropper").bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=5)
    page.mouse.up()
    crop = page.input_value("input[name=crop]")
    check("crop drawn", crop not in ("", "0.0000,0.0000,1.0000,1.0000"), crop)
    page.screenshot(path=OUT / "review.png", full_page=True)
    page.click("text=Créer le patron")
    page.wait_for_load_state()
    if "/imports/" in page.url:
        check("review form accepted", False, page.locator("p.warn, .field.err").all_inner_texts())
        return
    page.wait_for_selector("#modal h2")
    check("new pattern sheet opens", "E2E Test" in page.inner_text("#modal h2"))
    check("new pattern has its file", "Livret E2E Test.pdf" in page.inner_text("#modal .files"))
    page.click("text=Modifier la fiche")
    page.click("text=Supprimer le patron…")
    page.click("button.danger")
    page.wait_for_selector(".card")
    page.fill("#q", "E2E Test")
    page.wait_for_timeout(300)
    check("test pattern deleted", count(page) == 0)


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

        add_pattern(page)
        browser.close()

    check("no JavaScript errors", not errors, errors)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
