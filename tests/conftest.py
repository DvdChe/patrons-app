import pytest

from apps.accounts.models import User
from apps.catalog.models import Brand


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password="x")


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="Make My Lemonade")
