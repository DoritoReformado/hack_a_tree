#!/bin/bash
# Habilitar la extensión PostGIS en la base de datos predeterminada
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
EOSQL
