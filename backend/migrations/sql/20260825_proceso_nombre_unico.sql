CREATE UNIQUE INDEX IF NOT EXISTS uq_proceso_etl_repositorio_nombre
    ON proceso_etl (repositorio_id, LOWER(nombre));
