from django.db import models
from django.core.files.storage import FileSystemStorage
from django.contrib.auth.models import User
from django.db.models.signals import post_delete
from django.dispatch import receiver
import os

large_storage = FileSystemStorage(
    location='media',
    base_url='/media/'
)

class TempResult(models.Model):
    petition_key = models.CharField(max_length=255, unique=True)
    json_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]


def file_upload_path(instance, filename):
    """
    Guarda el archivo con el nombre del título del registro.
    Mantiene la extensión original.
    """
    ext = os.path.splitext(filename)[1]  # obtengo extensión
    # Reemplaza espacios por guiones o guiones bajos para evitar problemas
    safe_title = instance.title.replace(" ", "_")
    # Ruta final
    return f"datasets/{safe_title}{ext}"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    foto = models.ImageField(upload_to="profiles/", null=True, blank=True)

    def __str__(self):
        return self.user.username



class DataSet(models.Model):
    CATEGORY_DATASET = {
        0: "deforestacion",
        1: "parametros de cultivo",
        2: "climatologia",
    }

    SUBCATEGORY_DATASET = {
        0: {     # deforestacion
            0: "alertas",
            1: "historico",
            2: "cobertura_bosque",
            3: "frontera_agricola"
        },
        1: {     # parametros de cultivo
            0: "NDVI",
            1: "NDRE",
            2: "NDWI",
            3: "EVI",
            4: "SAVI"
        },
        2: {
            0: "clima"
        }
    }

    title = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    category = models.IntegerField(choices=CATEGORY_DATASET.items())

    subcategory = models.IntegerField(default=0)

    def clean(self):
        """Validar que la subcategoría pertenece a la categoría seleccionada"""
        if self.subcategory not in self.SUBCATEGORY_DATASET[self.category]:
            raise ValidationError("Subcategoría inválida para esta categoría")

    def __str__(self):
        return f"{self.title} ({self.CATEGORY_DATASET[self.category]} - {self.get_subcategory_label()})"

    def get_subcategory_label(self):
        return self.SUBCATEGORY_DATASET[self.category][self.subcategory]



class File_Model(models.Model):
    FILE_TYPES = {
        0: "Raster",
        1: "Polygon",
    }

    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=file_upload_path, storage=large_storage)
    date = models.DateTimeField()
    date_upload = models.DateTimeField(auto_now_add=True)
    file_type = models.IntegerField(choices=FILE_TYPES.items())
    source_crs = models.CharField(max_length=32)
    data_rules = models.JSONField()
    file_url = models.URLField(max_length=500, blank=True)
    dataset = models.ForeignKey(DataSet,on_delete=models.CASCADE,related_name="files")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.file and not self.file_url:
            self.file_url = self.file.url
            super().save(update_fields=["file_url"])

    def __str__(self):
        return self.title + ' ' + str(self.FILE_TYPES[self.file_type])


@receiver(post_delete, sender=File_Model)
def delete_file_on_remove(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)