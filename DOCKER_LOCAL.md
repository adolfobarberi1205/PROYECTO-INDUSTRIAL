# Ejecutar el sistema con Docker en este equipo

Esta configuración es para pruebas locales en Windows con Docker Desktop.
No es todavía una configuración para el servidor de la empresa.

## Qué se ejecuta

- **Frontend:** interfaz compilada de React/Vite, servida por Nginx.
- **Backend:** FastAPI, Uvicorn y el analizador Python existente.
- **PostgreSQL:** continúa instalado en Windows. No se crea otro contenedor
  de base de datos ni se copian, migran o sustituyen sus datos.

El navegador entra por `http://localhost:8080`. Nginx entrega la interfaz y
reenvía las solicitudes `/api/` al backend dentro de la red de Docker.
El backend accede al PostgreSQL de Windows mediante `host.docker.internal`.

## Antes de iniciar

1. Abrir Docker Desktop y esperar a que el motor esté funcionando con
   contenedores Linux.
2. Mantener iniciado el servicio PostgreSQL de Windows.
3. Conservar `backend/.env` con la conexión local que ya utiliza la aplicación.
   No reemplazarlo con la plantilla si ya está configurado.
4. Abrir una terminal en la carpeta principal del proyecto, donde está
   `compose.yaml`. Si Docker acaba de instalarse, abrir una terminal nueva.

El contenedor cambia únicamente el servidor de la conexión a
`host.docker.internal`; conserva usuario, contraseña, puerto, nombre de base y
opciones de conexión. No hace falta editar la conexión local para Docker.

En otro equipo se necesita una base con el esquema y datos correspondientes.
Compose no ejecuta migraciones ni restaura respaldos automáticamente.

## Primera ejecución

Desde la carpeta principal:

```powershell
docker compose up -d --build
docker compose ps
```

La primera vez descarga las imágenes base y dependencias; puede tardar varios
minutos según la conexión. No sube el código ni los datos a Docker Hub.

Cuando ambos servicios estén iniciados:

- Aplicación: http://localhost:8080
- Documentación de la API: http://localhost:8080/api/docs
- Comprobación del backend: http://localhost:8080/api/health

El estado saludable del backend confirma que la API responde, no que PostgreSQL
esté disponible. Abrir el catálogo de procesos documentados comprueba también
la comunicación con la base de datos.

## Uso diario

```powershell
# Iniciar sin volver a construir las imágenes
docker compose up -d

# Consultar el estado
docker compose ps

# Ver los últimos mensajes
docker compose logs --tail 100 backend frontend

# Detener sin borrar los contenedores
docker compose stop

# Reconstruir después de modificar el código o las dependencias
docker compose up -d --build
```

`docker compose down` elimina los contenedores y la red de esta aplicación,
pero no la base de Windows ni las carpetas locales montadas. Las imágenes
quedan disponibles para reutilizarlas. No es necesario limpiar volúmenes ni
restablecer Docker para detener la aplicación.

Evitar detener el backend mientras está analizando o guardando un proceso:
una operación en curso puede interrumpirse.

## Dónde queda cada cosa

| Información | Ubicación | Al recrear un contenedor |
| --- | --- | --- |
| Procesos documentados y metadatos persistidos | PostgreSQL de Windows | Se conservan |
| XML y complementos durante el análisis | `archivos_temporales/` del proyecto | Se conservan hasta su limpieza por la aplicación |
| Resultados JSON del análisis | `resultados_temporales/` del proyecto | Se conservan |
| Credenciales de conexión | `backend/.env`, montado en modo de solo lectura | Se conserva; no se copia a la imagen |
| Código y librerías del backend | Imagen del backend | Se reconstruyen cuando cambia el código |
| Interfaz compilada y configuración Nginx | Imagen del frontend | Se reconstruyen cuando cambia el código |

La eliminación del XML después de un análisis exitoso sigue siendo
responsabilidad de la aplicación. Docker no añade una limpieza automática de
archivos abandonados ni reemplaza la política de respaldos de PostgreSQL.
Los temporales y resultados compartidos son los mismos que usa la ejecución
directa: evitar analizar o limpiar los mismos archivos simultáneamente desde
las dos modalidades.

Las imágenes excluyen `.env`, XML corporativos, `kettle.properties`, JSON
temporales, informes, respaldos, el historial Git y dependencias instaladas
en Windows. El archivo de configuración y las carpetas se montan al iniciar,
no durante la construcción.

## Si algo no funciona

- **Docker no se reconoce:** abrir otra terminal después de instalar Docker.
- **El motor no responde:** esperar a que Docker Desktop termine de iniciar.
- **Falta `backend/.env`:** configurar la conexión local; Compose no crea un
  archivo con credenciales inventadas.
- **El catálogo falla pero la página abre:** comprobar PostgreSQL, credenciales
  y que el servidor acepte la conexión procedente de Docker. Una instalación
  limitada a conexiones locales podría requerir revisar `listen_addresses`,
  `pg_hba.conf` o el firewall con permisos del administrador. No abrir el
  puerto a todas las redes ni desactivar la autenticación para resolverlo.
- **El puerto 8080 está ocupado:** detener el otro servicio o cambiar solo el
  puerto local en `127.0.0.1:8080:80` dentro de `compose.yaml`.
- **No aparecen cambios del código:** reconstruir con `docker compose up -d --build`.

No compartir logs sin revisarlos: pueden contener nombres de procesos o rutas
de archivos aunque las credenciales no estén incluidas en las imágenes.

## Límites de esta configuración

Solo se publica el puerto 8080 en la propia computadora (`127.0.0.1`). El
backend no publica un puerto independiente en Windows. La configuración usa
HTTP local, sin autenticación nueva ni HTTPS; no debe exponerse directamente
en Internet o reutilizarse sin revisión en la red corporativa.

Nginx admite cargas de hasta 512 MiB, incluido el formulario. La aplicación
mantiene su propio límite de 500 MiB para el XML. El proxy permite solicitudes
de análisis largas, pero no incorpora una cola de trabajos. Uvicorn utiliza
un solo worker para evitar multiplicar el consumo de memoria del analizador.

Para volver al desarrollo directo se mantienen los comandos habituales:
`fastapi dev app/main.py` en `backend/` y `npm run dev` en `frontend/`.
La interfaz directa usa el puerto 5173 y la API directa el 8000. En Docker la
interfaz compilada usa `/api`; no depende de un backend directo abierto en Windows.
