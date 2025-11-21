from rest_framework import viewsets
from .models import *
from .serializers import DataSetSerializer, FileModelSerializer
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import check_password
from django.contrib.auth import get_user_model
from .authentication import CookieJWTAuthentication
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import action
import redis
import json
import geopandas as gpd
from .tasks import *
import uuid
import tempfile
import zipfile
import gzip
import base64

User = get_user_model() 
redis_manager_for_polygons = redis.Redis.from_url("redis://dragonfly:6379/0")
redis_manager_for_password_change = redis.Redis.from_url("redis://dragonfly:6379/3", decode_responses=True)


class DataSetViewSet(viewsets.ModelViewSet):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = DataSet.objects.all()
    serializer_class = DataSetSerializer


class FileModelViewSet(viewsets.ModelViewSet):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    queryset = File_Model.objects.all()
    serializer_class = FileModelSerializer

class UserMeView(APIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_superuser": user.is_superuser,
            "is_authenticated": True,
            "foto": user.profile.foto.url if hasattr(user, "profile") and user.profile.foto else None,
            "is_extensionista": hasattr(user, "extensionista_user") and user.extensionista_user
        })

class UserLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username_or_email = request.data.get("username")
        password = request.data.get("password")

        if "@" in username_or_email:
            try:
                user_obj = User.objects.get(email=username_or_email)
                username = user_obj.username
            except User.DoesNotExist:
                return Response({"error": "Correo no registrado"}, status=401)
        else:
            username = username_or_email

        user = authenticate(username=username, password=password)
        if not user:
            return Response({"error": "Credenciales inválidas"}, status=401)

        refresh = RefreshToken.for_user(user)

        response = Response({"success": True})
        response.set_cookie(
            key="access_token",
            value=str(refresh.access_token),
            httponly=True,
            secure=True,
            samesite="None",
        )

        return response

class UserLogoutView(APIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        response = Response({"success": True, "message": "Sesión cerrada"})
        response.delete_cookie("access_token")
        return response

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email")

        if not email:
            return Response({"error": "Email requerido"}, status=400)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Nunca dices que no existe → seguridad
            return Response({"success": True, "message": "Si el correo existe, se enviará un enlace"})

        # Crear token único
        token = str(uuid.uuid4())

        # Guardar en Redis
        redis_manager_for_password_change.set(
            f"RESET_PASSWORD:{token}",
            user.id,
            ex=900  # expira en 15 minutos
        )

        # Crear link de recuperación
        reset_url = f"{FRONT_URL}reset-password/{token}/"

        # Enviar email por Celery
        send_password_reset_email.delay(email, reset_url)

        return Response({"success": True, "message": "Si el correo existe, se enviará un enlace"})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, token):
        new_password = request.data.get("new_password")

        if not new_password:
            return Response({"error": "Contraseña requerida"}, status=400)

        user_id = redis_manager_for_password_change.get(f"RESET_PASSWORD:{token}")

        if not user_id:
            return Response({"error": "Token inválido o expirado"}, status=401)

        user = User.objects.get(id=user_id)

        if len(new_password) < 6:
            return Response({"error": "La contraseña debe tener mínimo 6 caracteres"}, status=400)

        user.set_password(new_password)
        user.save()

        redis_manager_for_password_change.delete(f"RESET_PASSWORD:{token}")

        return Response({"success": True, "message": "Contraseña actualizada"})

class UserUpdatePhotoView(APIView):
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        user = request.user

        if "foto" not in request.FILES:
            return Response({"error": "No se envió ninguna foto"}, status=400)

        foto = request.FILES["foto"]

        if not hasattr(user, "profile"):
            return Response({"error": "El usuario no tiene perfil asociado"}, status=500)

        user.profile.foto = foto
        user.profile.save()

        return Response({
            "success": True,
            "foto_url": user.profile.foto.url
        })


def loader_dataframe_from_file(file):
    name = file.name.lower()

    if name.endswith(".geojson") or name.endswith(".json"):
        data = json.load(file)
        gdf = gpd.GeoDataFrame.from_features(data["features"])
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        return gdf

    if name.endswith(".zip"):
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(file, "r") as zpf:
                zpf.extractall(tmpdir)
            
            gdf = gpd.read_file(tmpdir)
            if gdf.crs is None:
                gdf.set_crs("EPSG:4326", inplace=True)
            elif gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs("EPSG:4326")   
            return gdf

class UploadTempFile(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        if "file" not in request.FILES:
            return Response({"error": "No se envió el archivo"}, status=400)
        
        file = request.FILES["file"]

        try:
            gdf = loader_dataframe_from_file(file)
        except Exception as e:
            return Response({"error": f"Error leyendo archivo: {str(e)}"}, status=400)

        # Crear clave única
        cache_key = f"{uuid.uuid4()}-{uuid.uuid4()}"

        # Preparar objeto para Redis
        redis_petition = {
            "petition_key": cache_key,
            "status":"tarea iniciada",
            "load": 0
        }

        # Guardar en Redis por 120 segundos (2 minutos)
        redis_manager_for_polygons.set(
            name=f"TEMP:{cache_key}",
            value=json.dumps(redis_petition)
        )
        modelo_gdf.delay(gdf.to_json(), cache_key)
        return Response({
            "status": "petición realizada exitosamente",
            "petition_key": cache_key
        })



class GetTempFileStatus(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        petition_key = request.data.get("petition_key")
        redis_key = f"TEMP:{petition_key}"

        raw = redis_manager_for_polygons.get(redis_key)
        if not raw:
            return Response({"error": "Petición no encontrada o expirada"}, status=404)

        data = json.loads(raw)

        # Si no ha terminado → devolver solo progreso
        if data.get("status") != "Proceso Completado":
            return Response({
                "petition_key": petition_key,
                "status": data.get("status"),
                "load": data.get("load")
            })

        # Buscar el resultado en PostgreSQL
        try:
            temp = TempResult.objects.get(petition_key=petition_key)
            result = temp.json_data
        except TempResult.DoesNotExist:
            result = None

        return Response({
            "petition_key": petition_key,
            "status": "Proceso Completado",
            "load": 100,
            "result": result
        })

