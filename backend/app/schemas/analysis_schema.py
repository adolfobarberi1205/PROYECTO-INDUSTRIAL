from pydantic import BaseModel, ConfigDict, Field


class SolicitudAnalisis(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    carpeta: str = Field(
        min_length=1,
        description="Carpeta del repositorio Pentaho.",
    )

    nombre_job: str = Field(
        min_length=1,
        description="Nombre del Job principal seleccionado.",
    )

    nombre_repositorio: str = Field(
        min_length=3,
        max_length=255,
        description="Nombre funcional elegido para el repositorio.",
    )

    repositorio_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Repositorio existente cuando la carga corresponde a una "
            "version o a un archivo ya registrado."
        ),
    )


class SeleccionProcesoLote(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    carpeta: str = Field(min_length=1)
    nombre_job: str = Field(min_length=1)
    nombre_proceso: str = Field(min_length=3, max_length=255)
    descripcion: str | None = Field(default=None, max_length=2000)


class SolicitudAnalisisLote(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    nombre_repositorio: str = Field(min_length=3, max_length=255)
    repositorio_id: int | None = Field(default=None, gt=0)
    procesos: list[SeleccionProcesoLote] = Field(
        min_length=1,
        max_length=50,
    )
