# Sistema de documentación ETL

Aplicación para analizar exportaciones de repositorios Pentaho, reconstruir
procesos a partir de sus Jobs principales y consultar su documentación técnica.

## Funcionalidades

- Carga temporal de repositorios y selección de uno o varios Jobs principales.
- Reconstrucción de Jobs y transformaciones alcanzables y diagramas navegables.
- Análisis de tablas FACT y DIM, campos, fuentes, variables y parámetros.
- Consulta de detalles técnicos y SQL documentado de las transformaciones.
- Guardado explícito de procesos en PostgreSQL y consulta posterior sin volver
  a cargar el XML original.
- Catálogo de repositorios y procesos documentados.

El sistema documenta metadatos mediante análisis estático. No ejecuta los procesos
ETL ni sus consultas SQL contra las bases de datos de origen. La integración con
un modelo de IA todavía no está implementada.

## Organización

- `backend/`: API FastAPI, servicios de análisis, persistencia y pruebas.
- `frontend/`: interfaz React, TypeScript y Vite.
- `legacy/`: motor Python original utilizado por el adaptador del backend.
- `backend/migrations/`: migraciones y scripts de evolución del esquema.

## Desarrollo local

Se necesita Python (el entorno de desarrollo utiliza 3.13), Node.js compatible
con la versión de Vite de `frontend/package.json`, npm y PostgreSQL. El motor
legacy importa Tkinter; debe estar disponible en la instalación de Python,
aunque el backend no abra la interfaz de escritorio.

### Backend

Desde la raíz del proyecto, en PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Editar `backend/.env` únicamente en el equipo local y configurar `DATABASE_URL`
con una cuenta autorizada de PostgreSQL. La contraseña de la plantilla es un
marcador y debe sustituirse. No incluir `.env` en Git.

La base de datos debe tener el esquema correspondiente a la versión del sistema.
Revisar `backend/migrations/` con el responsable de la base antes de aplicar
cambios: hay migraciones Alembic y scripts SQL separados. Este repositorio no
incluye un respaldo de datos ni garantiza una instalación completa del esquema
mediante un único comando.

Desde `backend/`:

```powershell
fastapi dev app/main.py
```

API local: http://127.0.0.1:8000. Documentación: http://127.0.0.1:8000/docs.

### Frontend

En otra terminal, desde la raíz:

```powershell
cd frontend
npm ci
npm run dev
```

Interfaz local: http://localhost:5173.

Para comprobar la compilación del frontend: `npm run build` desde `frontend/`.
Las pruebas manuales `backend/tests/prueba_*.py` pueden requerir archivos locales
o PostgreSQL; no deben ejecutarse indiscriminadamente sobre una base productiva.

## Flujo de uso

1. Cargar una exportación Pentaho y, opcionalmente, archivos complementarios.
2. Seleccionar la carpeta y el Job principal que delimita el proceso.
3. Reconstruir y revisar diagramas, tablas y linaje disponible.
4. Asignar un nombre funcional y documentar el proceso en PostgreSQL.
5. Abrir los procesos guardados desde el catálogo, agrupado por repositorio.

## Archivos que no se incluyen

El repositorio conserva código, pruebas y configuración de ejemplo. Se excluyen
credenciales, XML, `kettle.properties`, cargas y resultados temporales, informes
extraídos de datos corporativos, respaldos, entornos virtuales y dependencias
instaladas. Los archivos locales excluidos no se borran al configurar Git.

No subir datos corporativos ni secretos, aunque el repositorio sea privado.
Las muestras de pruebas deben ser sintéticas o contar con autorización.

## Despliegue

También se puede ejecutar localmente con Docker Desktop: dos contenedores para
frontend y backend, conservando el PostgreSQL instalado en Windows.
Consultar [DOCKER_LOCAL.md](DOCKER_LOCAL.md) para iniciar, detener y actualizar
la aplicación y conocer dónde se conservan los datos.

Esta configuración no es un despliegue corporativo. Antes de publicar en un
servidor deben revisarse autenticación, HTTPS, gestión de secretos, permisos,
redes, respaldos y los límites de procesamiento de archivos.
