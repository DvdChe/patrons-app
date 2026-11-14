import pytest

from apps.accounts.models import User
from apps.catalog.models import Brand


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password="x")


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="Make My Lemonade")


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """The manifest storage needs `collectstatic`, which tests do not run."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture(autouse=True)
def no_llm(settings):
    """Tests never call the real LLM (compose sets LLM_URL for development)."""
    settings.LLM_URL = ""
