import pytest

from apps.catalog.text import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Élise", "elise"), ("Cœur", "coeur"), ("Ça va", "ca va"), ("Æther", "aether")],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected
