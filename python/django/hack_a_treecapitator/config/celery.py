import os
from celery import Celery
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tree_capitator.settings")
django.setup()

app = Celery("tree_capitator")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
