"""Import of the pattern files (PDF, images, zips) from the MVP Patrons folder. Synthetic data."""

import hashlib
import unicodedata
import zipfile

import pytest
from django.core.files.storage import Storage

from apps.catalog.legacy.files import file_kind, import_files, matches_pattern
from apps.catalog.models import Pattern, PatternFile


@pytest.fixture(autouse=True)
def media(settings, tmp_path_factory):
    settings.MEDIA_ROOT = tmp_path_factory.mktemp("media")
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
    return settings.MEDIA_ROOT


@pytest.fixture
def patrons(tmp_path):
    return tmp_path / "Patrons"


def write(path, content=b"%PDF-1.4 fake"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_pattern(user, brand, ref, name, location):
    return Pattern.objects.create(
        owner=user,
        brand=brand,
        name=name,
        garment_type="Robe",
        legacy_ref=ref,
        legacy_location=location,
    )


def names(pattern):
    return sorted(f.original_name for f in pattern.files.all())


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Livret Rosa.pdf", PatternFile.Kind.BOOKLET),
        ("SewingInstructions.pdf", PatternFile.Kind.BOOKLET),
        ("Notice & Planches.pdf", PatternFile.Kind.BOOKLET),
        ("Gamme de montage.pdf", PatternFile.Kind.BOOKLET),
        ("GDM_605.pdf", PatternFile.Kind.BOOKLET),
        ("A0-BackToBasics-Backpack.pdf", PatternFile.Kind.PIECES),
        ("Rosa_A4.pdf", PatternFile.Kind.PIECES),
        ("Planches US Letter.pdf", PatternFile.Kind.PIECES),
        ("Rosa projecteur.pdf", PatternFile.Kind.PIECES),
        ("dessin technique.jpg", PatternFile.Kind.IMAGE),
        ("cover.PNG", PatternFile.Kind.IMAGE),
        ("A0.zip", PatternFile.Kind.ARCHIVE),
        ("100 Georgia jacket.pdf", PatternFile.Kind.OTHER),
        ("lisez-moi.docx", PatternFile.Kind.OTHER),
    ],
)
def test_file_kind(name, kind):
    assert file_kind(name) == kind


@pytest.mark.parametrize(
    ("model", "filename", "expected"),
    [
        ("Moira skirt", "101 Moira skirt.pdf", True),
        ("Tina top", "101 Moira skirt.pdf", False),
        ("Cowl Neck Top & Sleeveless Blouse", "A0-BackToBasics-CowlNeckTopAndSleevelessBlouse.pdf", True),
        ("Kota", "KOTA-FABRICARTEM-Patron-PDF-eb80ai.pdf", True),
        ("Iris (combinaison)", "Iris.pdf", True),
        ("Élise", "elise-A4.pdf", True),
        # Whole words only: a short name must not match inside a longer word
        ("Liv", "Livret_IVY_EvelynePatterns.pdf", False),
        ("Liv", "A0_LIV_EvelynePatterns.pdf", True),
        ("Ivy", "Livret_IVY_EvelynePatterns.pdf", True),
        ("L’Harmonieuse", "LIVRET PAS A PAS Robe L_harmonieuse - Copie.pdf", True),
        ("Men’s Cargo Shorts", "A0-BackToBasics-MensCargoShorts.pdf", True),
    ],
)  # fmt: skip
def test_matches_pattern(model, filename, expected):
    assert matches_pattern(model, filename) is expected


def test_imports_a_folder(user, brand, patrons, media):
    write(patrons / "Robes/Rosa/Livret Rosa.pdf", b"booklet")
    write(patrons / "Robes/Rosa/A4/Rosa_A4.pdf", b"pieces")
    write(patrons / "Robes/Rosa/.DS_Store", b"junk")
    p = make_pattern(user, brand, 1, "Rosa", "Documents/Patrons/Robes/Rosa")

    report = import_files(patrons, user)

    assert names(p) == ["A4/Rosa_A4.pdf", "Livret Rosa.pdf"]
    booklet = p.files.get(original_name="Livret Rosa.pdf")
    assert booklet.kind == PatternFile.Kind.BOOKLET
    assert booklet.size == len(b"booklet")
    assert booklet.sha256 == hashlib.sha256(b"booklet").hexdigest()
    assert booklet.file.read() == b"booklet"
    assert report.files_linked == 2
    assert report.blobs_uploaded == 2


def test_zip_next_to_its_extracted_folder_is_skipped(user, brand, patrons):
    write(patrons / "Jupes/Pack/lavezzi/Livret.pdf", b"booklet")
    write(patrons / "Jupes/Pack/lavezzi.zip", b"same content, zipped")
    p = make_pattern(user, brand, 1, "Lavezzi", "Documents/Patrons/Jupes/Pack")

    import_files(patrons, user)

    assert names(p) == ["lavezzi/Livret.pdf"]


def test_imports_zip_members(user, brand, patrons):
    zpath = patrons / "Top/blouse.zip"
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("blouse/Notice.pdf", b"notice")
        z.writestr("__MACOSX/blouse/._Notice.pdf", b"junk")
        z.writestr("blouse/", b"")
    p = make_pattern(user, brand, 1, "Blouse", "Documents/Patrons/Top/blouse.zip")

    import_files(patrons, user)

    assert names(p) == ["blouse/Notice.pdf"]
    assert p.files.get().file.read() == b"notice"


def test_shared_folder_files_go_to_the_matching_pattern(user, brand, patrons):
    folder = "Documents/Patrons/Mag/Issue 57"
    write(patrons / "Mag/Issue 57/101 Moira skirt.pdf", b"moira")
    write(patrons / "Mag/Issue 57/102 Tina top.pdf", b"tina")
    write(patrons / "Mag/Issue 57/cover.pdf", b"cover")
    moira = make_pattern(user, brand, 1, "Moira skirt", folder)
    tina = make_pattern(user, brand, 2, "Tina top", folder)
    lost = make_pattern(user, brand, 3, "Delia skirt", folder)

    report = import_files(patrons, user)

    assert names(moira) == ["101 Moira skirt.pdf"]
    assert names(tina) == ["102 Tina top.pdf"]
    assert names(lost) == []
    assert any("Delia skirt" in w for w in report.warnings)
    assert any("cover.pdf" in w for w in report.warnings)  # linked to no pattern


def test_unlinked_shared_files_are_not_reported_for_a_partial_run(user, brand, patrons):
    folder = "Documents/Patrons/Mag/Issue 57"
    write(patrons / "Mag/Issue 57/101 Moira skirt.pdf", b"moira")
    write(patrons / "Mag/Issue 57/102 Tina top.pdf", b"tina")
    make_pattern(user, brand, 1, "Moira skirt", folder)
    make_pattern(user, brand, 2, "Tina top", folder)

    report = import_files(patrons, user, refs={1})

    assert report.warnings == []


def test_identical_files_are_stored_once_and_reimport_uploads_nothing(user, brand, patrons, media):
    write(patrons / "A/Rosa.pdf", b"same")
    write(patrons / "B/Rosa copie.pdf", b"same")
    a = make_pattern(user, brand, 1, "Rosa", "Documents/Patrons/A/Rosa.pdf")
    b = make_pattern(user, brand, 2, "Rosa 2", "Documents/Patrons/B/Rosa copie.pdf")

    first = import_files(patrons, user)
    second = import_files(patrons, user)

    assert first.blobs_uploaded == 1
    assert a.files.get().file.name == b.files.get().file.name
    assert second.blobs_uploaded == 0
    assert second.files_linked == 0
    assert PatternFile.objects.count() == 2
    assert sum(1 for f in media.rglob("*") if f.is_file()) == 1


def test_changed_file_is_replaced(user, brand, patrons):
    path = write(patrons / "A/Rosa.pdf", b"v1")
    p = make_pattern(user, brand, 1, "Rosa", "Documents/Patrons/A/Rosa.pdf")
    import_files(patrons, user)
    path.write_bytes(b"v2")

    import_files(patrons, user)

    assert p.files.get().file.read() == b"v2"


def test_location_resolution(user, brand, patrons):
    # Zip whose location is written without ".zip"
    with zipfile.ZipFile(write(patrons / "Top/Irma.zip", b"").parent / "Irma.zip", "w") as z:
        z.writestr("Irma.pdf", b"irma")
    irma = make_pattern(user, brand, 1, "Irma", "Documents/Patrons/Top/Irma")
    # Folder name stored with a different Unicode normalization (macOS NFD)
    write(patrons / unicodedata.normalize("NFD", "Robes/Élise") / "Élise.pdf", b"elise")
    elise = make_pattern(user, brand, 2, "Élise", "Documents/Patrons/Robes/Élise")

    import_files(patrons, user)

    assert names(irma) == ["Irma.pdf"]
    assert len(names(elise)) == 1


def test_missing_and_downloads_locations_are_reported(user, brand, patrons):
    patrons.mkdir()
    make_pattern(user, brand, 1, "Gone", "Documents/Patrons/Robes/Gone")
    make_pattern(user, brand, 2, "Later", "Downloads/later.pdf")

    report = import_files(patrons, user)

    text = "\n".join(report.warnings)
    assert "Gone" in text
    assert "Later" in text


def test_only_selected_refs(user, brand, patrons):
    write(patrons / "A/a.pdf")
    write(patrons / "B/b.pdf", b"other")
    a = make_pattern(user, brand, 1, "A", "Documents/Patrons/A/a.pdf")
    b = make_pattern(user, brand, 2, "B", "Documents/Patrons/B/b.pdf")

    import_files(patrons, user, refs={2})

    assert names(a) == []
    assert names(b) == ["b.pdf"]


def test_dry_run_writes_nothing(user, brand, patrons, media):
    write(patrons / "A/a.pdf", b"12345")
    p = make_pattern(user, brand, 1, "A", "Documents/Patrons/A/a.pdf")

    report = import_files(patrons, user, dry_run=True)

    assert report.files_linked == 1
    assert report.bytes_total == 5
    assert names(p) == []
    assert not any(f.is_file() for f in media.rglob("*"))


class TestDownload:
    @pytest.fixture
    def pattern_file(self, user, brand, patrons):
        write(patrons / "A/Livret.pdf", b"%PDF booklet")
        p = make_pattern(user, brand, 1, "A", "Documents/Patrons/A/Livret.pdf")
        import_files(patrons, user)
        return p.files.get()

    def test_owner_gets_the_file_inline(self, client, user, pattern_file):
        client.force_login(user)
        response = client.get(f"/files/{pattern_file.pk}")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"%PDF booklet"
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("inline")

    def test_other_user_gets_404(self, client, pattern_file, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="bob", password="x"))
        assert client.get(f"/files/{pattern_file.pk}").status_code == 404

    def test_sheet_lists_files(self, client, user, pattern_file):
        client.force_login(user)
        html = client.get(f"/patterns/{pattern_file.pattern_id}/sheet").content.decode()
        assert f"/files/{pattern_file.pk}" in html
        assert "Livret.pdf" in html


class FakeObjectStorage(Storage):
    """Stands for S3Storage: records the presigned URL request."""

    calls: list = []

    def exists(self, name):
        return True

    def url(self, name, parameters=None, expire=None):
        FakeObjectStorage.calls.append((name, parameters, expire))
        return f"https://s3.example/{name}?signed"


def test_download_from_object_storage_redirects_to_a_short_presigned_url(
    client, user, brand, settings
):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": f"{__name__}.FakeObjectStorage"},
    }
    p = make_pattern(user, brand, 1, "A", "Documents/Patrons/A")
    f = PatternFile.objects.create(
        pattern=p, file="files/1/ab/abc.svg", original_name="dessin.svg", sha256="abc", size=1
    )
    client.force_login(user)

    response = client.get(f"/files/{f.pk}")

    assert response.status_code == 302
    assert response.url == "https://s3.example/files/1/ab/abc.svg?signed"
    _, params, expire = FakeObjectStorage.calls[-1]
    assert expire == 300
    # SVG can run scripts: never displayed inline
    assert params["ResponseContentDisposition"].startswith("attachment")
    assert params["ResponseContentType"] == "image/svg+xml"
