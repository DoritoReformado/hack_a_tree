import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from django.core.mail import send_mail
import tempfile
from celery import shared_task
import os
import logging
from .models import DataSet, File_Model, TempResult
import rasterio
from rasterio.windows import from_bounds
import geopandas as gpd
from shapely.geometry import shape, mapping
import redis
import time
import json 
import numpy as np
from rasterio.mask import mask
from django.db.models import Q
import gzip
import base64
from django.utils import timezone
from datetime import timedelta
import pyproj
from shapely.ops import transform
import math
from shapely.geometry import mapping
import rasterio
from rasterio.mask import mask
import numpy as np
import requests
import pandas as pd
from shapely.geometry import mapping
from datetime import datetime

import openai
import os

openai.api_key = os.environ.get("OPENAI_API_KEY")  # Pon tu API key en la variable de entorno

redis_manager_for_polygons = redis.Redis.from_url("redis://dragonfly:6379/0", decode_responses=True)
@shared_task
def clean_temp_results():
    ttl_limit = timezone.now() - timedelta(hours=2)
    TempResult.objects.filter(created_at__lt=ttl_limit).delete()


@shared_task
def send_password_reset_email(email, url):
    send_mail(
        subject="Restablecer contraseña",
        message=f"Ingresa al siguiente enlace para restablecer tu contraseña:\n\n{url}",
        from_email=None,
        recipient_list=[email],
        fail_silently=False,
    )



@shared_task(bind=True)
def modelo_gdf(self, gdf_json, petition_key):

    gdf_dict = json.loads(gdf_json)
    gdf = gpd.GeoDataFrame.from_features(gdf_dict["features"])
    gdf.set_crs("EPSG:4326", inplace=True)

    gdf = gdf.to_crs(3116)
    gdf["area_ha"] = gdf.geometry.area/10000
    gdf = gdf.to_crs(4326)
    total = len(gdf)

    # Caso sin datos
    if total == 0:
        TempResult.objects.create(
            petition_key=petition_key,
            data=[]
        )
        redis_manager_for_polygons.set(
            name=f"TEMP:{petition_key}",
            value=json.dumps({
                "petition_key": petition_key,
                "status": "sin datos",
                "load": 100
            }),
            ex=300
        )
        return
    gdf["deforestation_history"] = None
    gdf["deforestation_hansen"] = None
    gdf["index_crops"] = None
    gdf["wheather"] = None
    # Iterar poligonos
    for i, row in gdf.iterrows():
        polygon = row.geometry
        defo_stats = analyze_deforestation_history(polygon)
        gdf.at[i, "deforestation_history"] = defo_stats
        defo_stats_1 = analyze_deforestation_by_raster_values(polygon)
        gdf.at[i, "deforestation_hansen"] = defo_stats_1
        index_crops = analyze_index_history(polygon)
        gdf.at[i, "index_crops"] = index_crops
        wheather = download_weather_today(polygon)
        gdf.at[i, "wheather"] = wheather
        
        load = round((i + 1)/total*100, 2)
        status = f"procesando poligono {i+1}/{total}"

        redis_manager_for_polygons.set(
            name=f"TEMP:{petition_key}",
            value=json.dumps({
                "petition_key": petition_key,
                "status": status,
                "load": load
            }),
            ex=300
        )

    gdf = add_descriptions_to_gdf(gdf)

    # Convertir resultado final
    final_json = json.loads(gdf.to_json())

    # Guardar resultado grande en PostgreSQL
    TempResult.objects.create(
        petition_key=petition_key,
        json_data=final_json
    )

    # Último estado
    redis_manager_for_polygons.set(
        name=f"TEMP:{petition_key}",
        value=json.dumps({
            "petition_key": petition_key,
            "status": "Proceso Completado",
            "load": 100
        }),
        ex=300
    )




def analyze_deforestation_history(polygon):
    # -----------------------------
    # 1. Cargar dataset histórico
    # -----------------------------
    datasets = DataSet.objects.filter(category=0, subcategory=1)
    historic_files = File_Model.objects.filter(dataset__in=datasets).order_by("date")

    if not historic_files.exists():
        return {"history": [], "total_def_area_ha": 0}

    results = []
    previous_mask = None
    total_area_ha = 0
    polygon_geojson = [mapping(polygon)]

    for file_obj in historic_files:
        year = file_obj.date.year

        try:
            with rasterio.open(file_obj.file.path) as src:
                out_image, out_transform = mask(src, polygon_geojson, crop=True)
                raster = out_image[0].astype("float32")
                raster[raster == src.nodata] = np.nan

                current_mask = (raster == 0.5)

                if previous_mask is None:
                    previous_mask = current_mask
                    continue

                new_def_pixels = np.logical_and(current_mask, np.logical_not(previous_mask))
                count_pixels = np.sum(new_def_pixels)

                # -----------------------------
                # Aproximación rápida del área
                # -----------------------------
                if count_pixels > 0:
                    nrows, ncols = raster.shape
                    xs, ys = rasterio.transform.xy(out_transform, 0, 0)
                    lat_center = np.mean([ys, ys + out_transform.e * nrows])

                    # Tamaño del píxel en grados
                    dx = abs(out_transform.a)
                    dy = abs(out_transform.e)

                    pixel_area_m2 = 10 * 10  # píxel 10x10 m
                    area_ha = count_pixels * pixel_area_m2 / 10000
                else:
                    area_ha = 0

                results.append({
                    "year": year,
                    "new_def_area_ha": float(area_ha),
                    "pixels_detected": int(count_pixels)
                })

                total_area_ha += area_ha
                previous_mask = current_mask

        except Exception as e:
            print("Error procesando raster:", file_obj.file.path, str(e))
            continue

    return {"history": results, "total_def_area_ha": float(total_area_ha)}


def analyze_deforestation_by_raster_values(polygon):
    datasets = DataSet.objects.filter(category=0, subcategory=0)
    historic_files = File_Model.objects.filter(dataset__in=datasets).order_by("date")
    
    if not historic_files.exists():
        return {"history": [], "total_def_area_ha": 0}

    results = []
    total_area_ha = 0
    polygon_geojson = [mapping(polygon)]

    for file_obj in historic_files:
        try:
            with rasterio.open(file_obj.file.path) as src:
                # Recorte del raster al polígono
                out_image, out_transform = mask(src, polygon_geojson, crop=True)
                raster = out_image[0].astype("float32")
                raster[raster == src.nodata] = np.nan
                years = range(1, 25)
                for val in years:
                    # Año real
                    year = 2000 + val

                    # Píxeles que ocurrieron en ese año
                    mask_year = (raster == val)
                    count_pixels = np.sum(mask_year)

                    if count_pixels > 0:
                        # Aproximación rápida del área
                        pixel_area_m2 = 10 * 10  # píxel 10x10 m
                        area_ha = count_pixels * pixel_area_m2 / 10000
                    else:
                        area_ha = 0

                    if area_ha > 0:
                        results.append({
                            "year": year,
                            "new_def_area_ha": float(area_ha),
                            "pixels_detected": int(count_pixels)
                        })
                        total_area_ha += area_ha

        except Exception as e:
            print("Error procesando raster:", file_obj.file.path, str(e))
            continue

    return {"history": results, "total_def_area_ha": float(total_area_ha)}

def analyze_index_history(polygon):
    datasets = DataSet.objects.filter(category=1)
    historic_files = File_Model.objects.filter(dataset__in=datasets).order_by("date")
    subcategory_dict = DataSet.SUBCATEGORY_DATASET[1]
    if not historic_files.exists():
        return {"indices": [], "total_mean_value": 0}

    results = []
    total_mean_value = 0
    polygon_geojson = [mapping(polygon)]

    # Iteramos por subcategorías disponibles en los datasets
    subcategories = datasets.values_list("subcategory", flat=True).distinct()

    for subcat in subcategories:
        # Filtrar archivos por subcategoría
        files_subcat = historic_files.filter(dataset__subcategory=subcat)
        subcat_values = []

        for file_obj in files_subcat:
            try:
                with rasterio.open(file_obj.file.path) as src:
                    # Recorte del raster al polígono
                    out_image, _ = mask(src, polygon_geojson, crop=True)
                    raster = out_image[0].astype("float32")
                    raster[raster == src.nodata] = np.nan

                    # Aplicar trim: descartar valores menores que 0.5
                    raster_trimmed = np.where(raster >= 0.5, raster, np.nan)

                    # Calcular valor promedio del raster recortado
                    mean_value = np.nanmean(raster_trimmed)
                    if not np.isnan(mean_value):
                        subcat_values.append(mean_value)

            except Exception as e:
                print("Error procesando raster:", file_obj.file.path, str(e))
                continue

        # Promedio final de la subcategoría
        if subcat_values:
            mean_subcat = float(np.mean(subcat_values))
            results.append({
                "subcategory": subcategory_dict[int(subcat)],
                "mean_value": mean_subcat
            })

    return {
        "indices": results,
    }



def download_weather_today(polygon, timezone="America/Bogota"):
    # ---------------------
    # Validación del polígono
    # ---------------------
    if not polygon.is_valid or polygon.is_empty:
        print("Polígono inválido o vacío")
        return {}

    centroid = polygon.centroid
    lat, lon = centroid.y, centroid.x

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        print("Coordenadas fuera de rango")
        return {}

    # ---------------------
    # Fechas: solo hoy
    # ---------------------
    today = datetime.utcnow().date()
    start_date = today.isoformat()
    end_date = today.isoformat()

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": (
            "temperature_2m_max,"
            "temperature_2m_min,"
            "precipitation_sum,"
            "shortwave_radiation_sum,"
            "relative_humidity_2m_mean,"
            "wind_speed_10m_mean"
        ),
        "timezone": timezone
    }

    # ---------------------
    # Llamada segura
    # ---------------------
    try:
        response = requests.get(url, params=params, timeout=10)
        time.sleep(1)
        response.raise_for_status()
        data = response.json().get("daily", {})

        if not data:
            print("No hay datos para hoy")
            return {}

        return data

    except requests.exceptions.Timeout:
        print("Timeout al conectar con Open-Meteo")
        return {}
    except requests.exceptions.RequestException as e:
        print("Error al solicitar datos de Open-Meteo:", e)
        return {}
    except Exception as e:
        print("Error inesperado:", e)
        return {}



def add_descriptions_to_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Añade una columna 'description' al GeoDataFrame usando GPT-3.0
    para generar un resumen humanizado de cada feature.
    """
    descriptions = []

    for i, row in gdf.iterrows():
        # Información base para el prompt
        fid = row.get("id", i)
        area = row.get("area_ha", 0)
        hansen = row.get("deforestation_hansen", {})
        hansen_total = hansen.get("total_def_area_ha", 0)
        history = row.get("deforestation_history", {})
        history_total = history.get("total_def_area_ha", 0)
        indices = row.get("index_crops", {}).get("indices", [])
        weather = row.get("wheather", {})

        # Construir prompt para GPT
        prompt = f"""
        Genera una descripción concisa y clara para un polígono agrícola con la siguiente información:

        - ID: {fid}
        - Área: {area:.2f} ha
        - Deforestación Hansen histórica: {hansen_total:.2f} ha
        - Deforestación reciente: {history_total:.2f} ha
        - Índices de cultivos: {', '.join([f"{idx['subcategory']}: {idx['mean_value']:.2f}" for idx in indices]) if indices else 'sin información'}
        - Clima: {weather if weather else 'sin información'}

        La descripción debe ser en lenguaje natural y fácil de entender.
        """

        try:
            response = openai.Completion.create(
                model="text-davinci-003",
                prompt=prompt,
                max_tokens=150,
                temperature=0.7,
            )
            description = response.choices[0].text.strip()
        except Exception as e:
            print(f"Error generando descripción GPT: {e}")
            description = "Descripción no disponible"

        descriptions.append(description)

    gdf["description"] = descriptions
    return gdf
