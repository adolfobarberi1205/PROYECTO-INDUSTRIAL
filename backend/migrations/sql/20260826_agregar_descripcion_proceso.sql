ALTER TABLE proceso_etl
    ADD COLUMN IF NOT EXISTS descripcion TEXT;

COMMENT ON COLUMN proceso_etl.descripcion IS
    'Descripcion funcional opcional agregada por el usuario.';
