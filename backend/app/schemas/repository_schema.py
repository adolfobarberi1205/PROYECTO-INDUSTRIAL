from pydantic import BaseModel, ConfigDict, Field


class SolicitudRenombrarRepositorio(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    nombre: str = Field(
        min_length=3,
        max_length=255,
        description="Nombre funcional visible del repositorio.",
    )
