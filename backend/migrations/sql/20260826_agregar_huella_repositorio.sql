ALTER TABLE repositorio
    ADD COLUMN IF NOT EXISTS huella_sha256 VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_repositorio_huella_sha256
    ON repositorio (huella_sha256)
    WHERE huella_sha256 IS NOT NULL;

COMMENT ON COLUMN repositorio.huella_sha256 IS
    'Huella SHA-256 del contenido completo del XML de Pentaho.';
