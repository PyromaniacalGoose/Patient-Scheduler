"""Test-only configuration; production settings are untouched."""
import os
from infra.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("TEST_DB_NAME", "patient_scheduler"),
        "USER": os.getenv("TEST_DB_USER", "scheduler_app"),
        "PASSWORD": os.getenv("TEST_DB_PASSWORD", "choose-a-real-password-here"),
        "HOST": os.getenv("TEST_DB_HOST", "localhost"),
        "PORT": os.getenv("TEST_DB_PORT", "5432"),
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
