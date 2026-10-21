from pydantic import BaseModel, ConfigDict, Field


class SolicitudDocumentacion(BaseModel):
    """Nombre funcional elegido para guardar un proceso ETL."""

    model_config = ConfigDict(str_strip_whitespace=True)

    nombre_proceso: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "Nombre funcional del proceso; no tiene que coincidir "
            "con el Job principal."
        ),
    )

    descripcion: str | None = Field(
        default=None,
        max_length=2000,
        description="Descripcion funcional opcional del proceso.",
    )


class SolicitudActualizacionProceso(BaseModel):
    """Datos editables de un proceso ya documentado."""

    model_config = ConfigDict(str_strip_whitespace=True)

    nombre: str = Field(
        min_length=3,
        max_length=500,
        description="Nombre funcional visible del proceso ETL.",
    )
    descripcion: str | None = Field(
        default=None,
        max_length=2000,
        description="Descripcion funcional opcional del proceso.",
    )


class ItemDocumentacionLote(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id_analisis: str = Field(min_length=32, max_length=32)
    nombre_proceso: str = Field(min_length=3, max_length=255)
    descripcion: str | None = Field(default=None, max_length=2000)


class SolicitudDocumentacionLote(BaseModel):
    procesos: list[ItemDocumentacionLote] = Field(
        min_length=1,
        max_length=50,
    )
