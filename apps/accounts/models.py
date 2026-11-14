from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model from day one: swapping it later requires a painful migration."""
