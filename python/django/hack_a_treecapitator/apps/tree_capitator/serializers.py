from rest_framework import serializers
from .models import *

class FileModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = File_Model
        fields = '__all__'

class DataSetSerializer(serializers.ModelSerializer):
    files = FileModelSerializer(many=True, read_only=True)
    class Meta:
        model = DataSet
        fields = '__all__'

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = '__all__'

