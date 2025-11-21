# apps/tree_capitator/admin.py

from django.contrib import admin
from .models import DataSet, File_Model

admin.site.register(DataSet)
admin.site.register(File_Model)
