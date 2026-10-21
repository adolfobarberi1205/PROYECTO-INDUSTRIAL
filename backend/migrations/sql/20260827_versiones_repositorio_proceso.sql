ALTER TABLE repositorio
    ADD COLUMN IF NOT EXISTS fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_repositorio_nombre_normalizado
    ON repositorio (LOWER(nombre));

CREATE TABLE IF NOT EXISTS version_repositorio (
    id BIGSERIAL PRIMARY KEY,
    repositorio_id BIGINT NOT NULL,
    numero_version INTEGER NOT NULL,
    huella_sha256 VARCHAR(64) NOT NULL,
    fecha_carga TIMESTAMP NOT NULL DEFAULT NOW(),
    es_actual BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT fk_version_repositorio
        FOREIGN KEY (repositorio_id)
        REFERENCES repositorio(id)
        ON DELETE CASCADE,
    CONSTRAINT uq_version_repositorio_numero
        UNIQUE (repositorio_id, numero_version),
    CONSTRAINT uq_version_repositorio_huella
        UNIQUE (huella_sha256)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_version_actual_repositorio
    ON version_repositorio (repositorio_id)
    WHERE es_actual = TRUE;

INSERT INTO version_repositorio (
    repositorio_id,
    numero_version,
    huella_sha256,
    fecha_carga,
    es_actual
)
SELECT
    r.id,
    1,
    r.huella_sha256,
    r.fecha_carga,
    TRUE
FROM repositorio AS r
WHERE r.huella_sha256 IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM version_repositorio AS vr
      WHERE vr.huella_sha256 = r.huella_sha256
  );

ALTER TABLE proceso_etl
    ADD COLUMN IF NOT EXISTS version_repositorio_id BIGINT;

ALTER TABLE proceso_etl
    ADD COLUMN IF NOT EXISTS huella_estructura VARCHAR(64);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_proceso_version_repositorio'
    ) THEN
        ALTER TABLE proceso_etl
            ADD CONSTRAINT fk_proceso_version_repositorio
            FOREIGN KEY (version_repositorio_id)
            REFERENCES version_repositorio(id);
    END IF;
END $$;

UPDATE proceso_etl AS p
SET version_repositorio_id = vr.id
FROM version_repositorio AS vr
WHERE p.repositorio_id = vr.repositorio_id
  AND vr.es_actual = TRUE
  AND p.version_repositorio_id IS NULL;

CREATE TABLE IF NOT EXISTS version_proceso_etl (
    id BIGSERIAL PRIMARY KEY,
    proceso_id BIGINT NOT NULL,
    version_repositorio_id BIGINT NOT NULL,
    numero_version INTEGER NOT NULL,
    fecha_documentacion TIMESTAMP NOT NULL DEFAULT NOW(),
    estado VARCHAR(40) NOT NULL DEFAULT 'VIGENTE',
    es_actual BOOLEAN NOT NULL DEFAULT TRUE,
    huella_estructura VARCHAR(64),
    snapshot_documentacion JSONB,
    CONSTRAINT fk_version_proceso
        FOREIGN KEY (proceso_id)
        REFERENCES proceso_etl(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_version_proceso_repositorio
        FOREIGN KEY (version_repositorio_id)
        REFERENCES version_repositorio(id),
    CONSTRAINT uq_version_proceso_numero
        UNIQUE (proceso_id, numero_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_version_actual_proceso
    ON version_proceso_etl (proceso_id)
    WHERE es_actual = TRUE;

INSERT INTO version_proceso_etl (
    proceso_id,
    version_repositorio_id,
    numero_version,
    fecha_documentacion,
    estado,
    es_actual,
    huella_estructura
)
SELECT
    p.id,
    p.version_repositorio_id,
    1,
    COALESCE(r.fecha_carga, NOW()),
    'VIGENTE',
    TRUE,
    p.huella_estructura
FROM proceso_etl AS p
JOIN repositorio AS r ON r.id = p.repositorio_id
WHERE p.version_repositorio_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM version_proceso_etl AS vp
      WHERE vp.proceso_id = p.id
  );

COMMENT ON TABLE version_repositorio IS
    'Versiones identificadas por la huella del XML; el XML original no se almacena.';

COMMENT ON TABLE version_proceso_etl IS
    'Historial de metadatos sanitizados de cada proceso documentado.';
