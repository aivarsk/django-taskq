import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB"),
        "USER":  os.environ.get("POSTGRES_USER"),
        "PASSWORD":  os.environ.get("POSTGRES_PASSWORD"),
        "HOST":  os.environ.get("POSTGRES_HOST"),
        "PORT":  os.environ.get("POSTGRES_PORT"),
    }
}
INSTALLED_APPS = ("django_taskq",)
# SECRET_KEY = ""
