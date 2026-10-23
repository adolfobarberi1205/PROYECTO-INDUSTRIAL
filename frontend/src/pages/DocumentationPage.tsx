import {
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  CheckCircle2,
  ChevronRight,
  Database,
  Eye,
  FileText,
  GitBranch,
  Home,
  Import,
  LoaderCircle,
  Network,
  Pencil,
  Save,
  Search,
  Settings,
  Upload,
  Workflow,
  X,
} from 'lucide-react'

import './DocumentationPage.css'
import { API_BASE_URL } from '../config/api'

type ActivePage =
  | 'overview'
  | 'processes'
  | 'documented-summary'
  | 'diagrams'
  | 'lineage'
  | 'documentation'

type DocumentationPageProps = {
  analysisId: string | null
  documentedProcessId: number | null
  onNavigate: (page: ActivePage) => void
  onUpload: () => void
  onDocumentedProcessUpdated?: (process: {
    id: number
    nombre: string
    descripcion: string
  }) => void
}

type DocumentedProcess = {
  id: string
  nombre: string
  tipo: 'JOB' | 'TRANSFORMATION'
  ruta: string
  es_principal: boolean
  elementos: number
  hops: number
  tiene_diagrama: boolean
}

type WarehouseTableDetection = {
  proceso_id: string
  transformacion: string
  ruta_transformacion: string
  step: string
  tipo_step: string
  operacion: string
  criterio: string
  confianza: string
}

type WarehouseTable = {
  categoria: 'DIMENSION' | 'FACT'
  nombre: string
  nombre_resuelto: string
  esquema: string
  esquema_resuelto: string
  nombre_completo: string
  operaciones: string[]
  detecciones: WarehouseTableDetection[]
}

type ParticipatingDimension = {
  nombre: string
  nombre_resuelto: string
}

type FactDimensionRelation = {
  nombre: string
  nombre_resuelto: string
  nombre_completo: string
  total_dimensiones: number
  dimensiones: ParticipatingDimension[]
  total_campos: number
  campos: Array<{
    nombre: string
    campo_origen: string
  }>
  linaje_campos: Array<{
    campo_fact: string
    campo_flujo: string
    tipo_origen:
      | 'CLAVE_DIMENSION'
      | 'ATRIBUTO_DIMENSION'
      | 'CALCULO'
      | 'CONSULTA_SQL'
      | 'VARIABLE'
      | 'ARCHIVO_EXCEL'
      | 'DATOS_INTERNOS'
      | 'SCRIPT'
      | 'INFORMACION_SISTEMA'
      | 'NO_DETERMINADO'
    detalle: string
    campos_origen: string[]
    tablas_origen: string[]
    fuentes_origen: string[]
  }>
}

type DimensionConstruction = {
  nombre: string
  nombre_resuelto: string
  nombre_completo: string
  tablas_fuente: Array<{
    nombre: string
    esquema: string
  }>
  variables: string[]
  transformaciones_auxiliares: Array<{
    nombre: string
    ruta: string
  }>
  pasos: Array<{
    nombre: string
    tipo: string
    funcion: string
  }>
}

type DocumentationPreview = {
  id_analisis: string
  modo: 'preview' | 'documented'
  guardado: boolean
  analisis: {
    job_principal: string
    carpeta: string
    fecha_analisis: string | null
    estado: string
  }
  proceso_principal: {
    id: string
    nombre: string
    tipo: string
    ruta: string
    descripcion: string
  }
  resumen: {
    procesos: number
    jobs: number
    transformaciones: number
    relaciones: number
    diagramas: number
    elementos: number
    hops: number
  }
  procesos: DocumentedProcess[]
  relaciones_por_estado: Record<string, number>
  tablas_dwh: {
    disponible: boolean
    total: number
    total_dimensiones: number
    total_hechos: number
    dimensiones: WarehouseTable[]
    hechos: WarehouseTable[]
  }
  relaciones_fact_dim?: {
    disponible: boolean
    total_hechos: number
    total_relaciones: number
    hechos_sin_dimensiones: number
    hechos: FactDimensionRelation[]
  }
  construccion_dimensiones?: {
    disponible: boolean
    total_dimensiones: number
    dimensiones: DimensionConstruction[]
  }
  almacenamiento: {
    incluye: string[]
    excluye_por_ahora: string[]
  }
  linaje_persistente?: {
    variables: Array<{
      nombre: string
      alcance: string | null
      usado_en: number
    }>
    parametros: Array<{
      nombre: string
      declaraciones: number
      referencias: number
      llamadas: number
    }>
  }
}

type DocumentationSaveResponse = {
  id_analisis: string
  estado: 'documentado'
  proceso_id: number
  repositorio_id: number
  repositorio: string
  nombre: string
  job_principal: string
  guardado: {
    componentes: number
    relaciones: number
    diagramas: number
    elementos: number
    hops: number
    tablas_dwh: number
    dimensiones: number
    hechos: number
    campos_dwh: number
    relaciones_fact_dimension: number
    variables: number
    parametros: number
  }
}

async function readError(response: Response) {
  const data = await response.json().catch(() => null)

  return data?.detail ?? 'The documentation preview could not be loaded.'
}

function DocumentationPage({
  analysisId,
  documentedProcessId,
  onNavigate,
  onUpload,
  onDocumentedProcessUpdated,
}: DocumentationPageProps) {
  const [preview, setPreview] =
    useState<DocumentationPreview | null>(null)
  const [isLoading, setIsLoading] =
    useState(false)
  const [errorMessage, setErrorMessage] =
    useState('')
  const [searchText, setSearchText] =
    useState('')
  const [typeFilter, setTypeFilter] =
    useState<'all' | 'JOB' | 'TRANSFORMATION'>('all')
  const [selectedFactKey, setSelectedFactKey] =
    useState('')
  const [selectedDimensionKey, setSelectedDimensionKey] =
    useState('')
  const [processName, setProcessName] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [savedDocumentation, setSavedDocumentation] =
    useState<DocumentationSaveResponse | null>(null)
  const [isEditingProcess, setIsEditingProcess] = useState(false)
  const [editProcessName, setEditProcessName] = useState('')
  const [editProcessDescription, setEditProcessDescription] = useState('')
  const [isUpdatingProcess, setIsUpdatingProcess] = useState(false)
  const [updateProcessError, setUpdateProcessError] = useState('')

  useEffect(() => {
    if (!analysisId && !documentedProcessId) {
      setPreview(null)
      setSelectedFactKey('')
      setSelectedDimensionKey('')
      setProcessName('')
      setSaveError('')
      setSavedDocumentation(null)
      return
    }

    setProcessName('')
    setSaveError('')
    setSavedDocumentation(null)

    const controller = new AbortController()

    async function loadPreview() {
      setIsLoading(true)
      setErrorMessage('')

      try {
        const response = await fetch(
          documentedProcessId
            ? `${API_BASE_URL}/procesos-documentados/${documentedProcessId}/documentacion`
            : `${API_BASE_URL}/analisis/${analysisId}/documentacion/preview`,
          { signal: controller.signal },
        )

        if (!response.ok) {
          throw new Error(await readError(response))
        }

        const data =
          (await response.json()) as DocumentationPreview

        setPreview(data)
        setEditProcessName(data.proceso_principal.nombre)
        setEditProcessDescription(
          data.proceso_principal.descripcion ?? '',
        )
        setIsEditingProcess(false)
        setUpdateProcessError('')

        const firstFact = data.relaciones_fact_dim?.hechos?.[0]
        setSelectedFactKey(
          firstFact?.nombre_completo ||
            firstFact?.nombre_resuelto ||
            firstFact?.nombre ||
            '',
        )

        const firstDimension =
          data.construccion_dimensiones?.dimensiones?.[0]
        setSelectedDimensionKey(
          firstDimension?.nombre_completo ||
            firstDimension?.nombre_resuelto ||
            firstDimension?.nombre ||
            '',
        )
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }

        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'The documentation preview could not be loaded.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false)
        }
      }
    }

    void loadPreview()

    return () => controller.abort()
  }, [analysisId, documentedProcessId])

  const hasDocumentationSource = Boolean(
    analysisId || documentedProcessId,
  )

  async function saveDocumentation() {
    const normalizedName = processName.trim()

    if (!analysisId || normalizedName.length < 3 || isSaving) {
      return
    }

    setIsSaving(true)
    setSaveError('')

    try {
      const response = await fetch(
        `${API_BASE_URL}/analisis/${analysisId}/documentacion`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            nombre_proceso: normalizedName,
          }),
        },
      )

      if (!response.ok) {
        throw new Error(await readError(response))
      }

      const data =
        (await response.json()) as DocumentationSaveResponse

      setSavedDocumentation(data)
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : 'The process documentation could not be saved.',
      )
    } finally {
      setIsSaving(false)
    }
  }

  async function updateDocumentedProcess() {
    const normalizedName = editProcessName.trim()

    if (
      !documentedProcessId ||
      normalizedName.length < 3 ||
      isUpdatingProcess
    ) {
      return
    }

    setIsUpdatingProcess(true)
    setUpdateProcessError('')

    try {
      const response = await fetch(
        `${API_BASE_URL}/procesos-documentados/${documentedProcessId}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nombre: normalizedName,
            descripcion: editProcessDescription.trim() || null,
          }),
        },
      )

      if (!response.ok) {
        throw new Error(await readError(response))
      }

      const updated = (await response.json()) as {
        id: number
        nombre: string
        descripcion: string
      }

      setPreview((current) =>
        current
          ? {
              ...current,
              proceso_principal: {
                ...current.proceso_principal,
                nombre: updated.nombre,
                descripcion: updated.descripcion,
              },
            }
          : current,
      )
      setEditProcessName(updated.nombre)
      setEditProcessDescription(updated.descripcion)
      setIsEditingProcess(false)
      onDocumentedProcessUpdated?.(updated)
    } catch (error) {
      setUpdateProcessError(
        error instanceof Error
          ? error.message
          : 'The documented process could not be updated.',
      )
    } finally {
      setIsUpdatingProcess(false)
    }
  }

  const filteredProcesses = useMemo(() => {
    if (!preview) {
      return []
    }

    const search = searchText.trim().toLocaleLowerCase()

    return preview.procesos.filter((process) => {
      const matchesType =
        typeFilter === 'all' || process.tipo === typeFilter
      const matchesSearch =
        !search ||
        process.nombre.toLocaleLowerCase().includes(search) ||
        process.ruta.toLocaleLowerCase().includes(search)

      return matchesType && matchesSearch
    })
  }, [preview, searchText, typeFilter])

  const selectedFactRelation = useMemo(() => {
    const facts = preview?.relaciones_fact_dim?.hechos ?? []

    return (
      facts.find(
        (fact) =>
          (fact.nombre_completo ||
            fact.nombre_resuelto ||
            fact.nombre) === selectedFactKey,
      ) ?? facts[0] ?? null
    )
  }, [preview, selectedFactKey])

  const selectedDimensionConstruction = useMemo(() => {
    const dimensions =
      preview?.construccion_dimensiones?.dimensiones ?? []

    return (
      dimensions.find(
        (dimension) =>
          (dimension.nombre_completo ||
            dimension.nombre_resuelto ||
            dimension.nombre) === selectedDimensionKey,
      ) ?? dimensions[0] ?? null
    )
  }, [preview, selectedDimensionKey])

  return (
    <div className="documentation-workspace">
      <aside className="sidebar documentation-sidebar">
        <div>
          <div className="sidebar-title">ETL UI</div>
          <div className="sidebar-subtitle">
            {documentedProcessId
              ? 'PROCESO DOCUMENTADO'
              : 'ANÁLISIS DE PROCESOS'}
          </div>

          <nav className="side-navigation">
            <button
              type="button"
              className="side-nav-item"
              onClick={() => onNavigate(
                documentedProcessId
                  ? 'documented-summary'
                  : 'overview',
              )}
            >
              <Home size={19} />
              {documentedProcessId ? 'Resumen' : 'Inicio'}
            </button>

            <button
              type="button"
              className="side-nav-item"
              onClick={() => onNavigate(
                documentedProcessId
                  ? 'diagrams'
                  : 'processes',
              )}
            >
              <Workflow size={19} />
              {documentedProcessId ? 'Diagramas' : 'Procesos'}
            </button>

            <button
              type="button"
              className="side-nav-item"
              onClick={() => onNavigate('lineage')}
            >
              <GitBranch size={19} />
              Linaje
            </button>

            <button
              type="button"
              className="side-nav-item active"
            >
              <FileText size={19} />
              Documentación
            </button>
          </nav>
        </div>

        <div className="sidebar-footer">
          <button
            type="button"
            className="import-button"
            onClick={onUpload}
          >
            <Import size={17} />
            {documentedProcessId
              ? 'Nuevo análisis'
              : 'Importar proceso'}
          </button>

          <div className="sidebar-tools">
            <Upload size={17} />
            <Settings size={17} />
          </div>
        </div>
      </aside>

      <main className="documentation-content">
        {!hasDocumentationSource && (
          <section className="documentation-empty">
            <FileText size={34} />
            <h1>Aún no hay vista previa</h1>
            <p>
              Reconstruct a root Job first, then use Document to review
              exactly what will be stored.
            </p>
            <button type="button" onClick={() => onNavigate('processes')}>
              Go to Processes
              <ChevronRight size={17} />
            </button>
          </section>
        )}

        {hasDocumentationSource && isLoading && (
          <section className="documentation-loading">
            Preparing documentation preview...
          </section>
        )}

        {hasDocumentationSource && errorMessage && (
          <section className="documentation-error">
            {errorMessage}
          </section>
        )}

        {preview && !isLoading && (
          <>
            <section className="documentation-hero">
              <div className="documentation-hero-copy">
                <div className="documentation-label">
                  <Eye size={16} />
                  VISTA PREVIA DE DOCUMENTACIÓN
                </div>

                <h1>{preview.proceso_principal.nombre}</h1>
                <p>{preview.proceso_principal.ruta}</p>

                {preview.proceso_principal.descripcion && !isEditingProcess && (
                  <p className="documentation-process-description">
                    {preview.proceso_principal.descripcion}
                  </p>
                )}

                {documentedProcessId && !isEditingProcess && (
                  <button
                    type="button"
                    className="documentation-edit-process-button"
                    onClick={() => {
                      setUpdateProcessError('')
                      setIsEditingProcess(true)
                    }}
                  >
                    <Pencil size={15} />
                    Edit process information
                  </button>
                )}

                {documentedProcessId && isEditingProcess && (
                  <div className="documentation-process-editor">
                    <label>
                      <span>NOMBRE DEL PROCESO</span>
                      <input
                        value={editProcessName}
                        maxLength={500}
                        onChange={(event) =>
                          setEditProcessName(event.currentTarget.value)
                        }
                      />
                    </label>
                    <label>
                      <span>DESCRIPTION (OPTIONAL)</span>
                      <textarea
                        value={editProcessDescription}
                        maxLength={2000}
                        rows={4}
                        placeholder="Describe the purpose and business scope of this ETL process..."
                        onChange={(event) =>
                          setEditProcessDescription(event.currentTarget.value)
                        }
                      />
                    </label>
                    {updateProcessError && (
                      <div className="documentation-process-editor-error">
                        {updateProcessError}
                      </div>
                    )}
                    <div className="documentation-process-editor-actions">
                      <button
                        type="button"
                        onClick={() => {
                          setEditProcessName(preview.proceso_principal.nombre)
                          setEditProcessDescription(
                            preview.proceso_principal.descripcion ?? '',
                          )
                          setUpdateProcessError('')
                          setIsEditingProcess(false)
                        }}
                        disabled={isUpdatingProcess}
                      >
                        <X size={15} />
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="save"
                        onClick={() => void updateDocumentedProcess()}
                        disabled={
                          editProcessName.trim().length < 3 ||
                          isUpdatingProcess
                        }
                      >
                        {isUpdatingProcess ? (
                          <LoaderCircle size={15} className="documentation-spin" />
                        ) : (
                          <Save size={15} />
                        )}
                        {isUpdatingProcess ? 'Saving...' : 'Save changes'}
                      </button>
                    </div>
                  </div>
                )}

                <div className="documentation-badges">
                  <span>
                    {savedDocumentation || preview.guardado
                      ? 'DOCUMENTED'
                      : 'PREVIEW'}
                  </span>
                  <span>
                    {savedDocumentation || preview.guardado
                      ? 'SAVED'
                      : 'NOT SAVED'}
                  </span>
                  <span>METADATOS SANITIZADOS</span>
                </div>
              </div>

              <div className="documentation-preview-note">
                <Database size={22} />
                <strong>
                  {savedDocumentation || preview.guardado
                    ? 'Stored in PostgreSQL'
                    : 'Database preview'}
                </strong>
                <p>
                  {savedDocumentation || preview.guardado
                    ? 'The process architecture and navigable diagrams are now permanent.'
                    : 'Review the process information, choose its functional name, and save it once.'}
                </p>
              </div>
            </section>

            {preview.linaje_persistente && (
              <section className="documentation-lineage-inventory">
                <div className="documentation-lineage-heading">
                  <div>
                    <span>LINAJE GUARDADO</span>
                    <h2>Variables y parámetros</h2>
                    <p>
                      Metadata recovered directly from PostgreSQL. Sensitive
                      values are not stored.
                    </p>
                  </div>
                  <GitBranch size={23} />
                </div>

                <div className="documentation-lineage-columns">
                  <article>
                    <header>
                      <strong>Variables</strong>
                      <b>
                        {preview.linaje_persistente.variables.length}
                      </b>
                    </header>
                    <div>
                      {preview.linaje_persistente.variables.map((variable) => (
                        <span key={variable.nombre}>
                          <strong>{variable.nombre}</strong>
                          <small>
                            {variable.alcance || 'Referenced'} ·{' '}
                            {variable.usado_en} processes
                          </small>
                        </span>
                      ))}
                    </div>
                  </article>

                  <article>
                    <header>
                      <strong>Parameters</strong>
                      <b>
                        {preview.linaje_persistente.parametros.length}
                      </b>
                    </header>
                    <div>
                      {preview.linaje_persistente.parametros.map((parameter) => (
                        <span key={parameter.nombre}>
                          <strong>{parameter.nombre}</strong>
                          <small>
                            {parameter.declaraciones} declarations ·{' '}
                            {parameter.referencias} references
                          </small>
                        </span>
                      ))}
                    </div>
                  </article>
                </div>
              </section>
            )}

            <section className="documentation-metrics">
              <article>
                <span>PROCESOS</span>
                <strong>{preview.resumen.procesos}</strong>
                <small>{preview.resumen.diagramas} diagrams</small>
              </article>
              <article>
                <span>JOBS</span>
                <strong>{preview.resumen.jobs}</strong>
                <small>Reachable Jobs</small>
              </article>
              <article>
                <span>TRANSFORMACIONES</span>
                <strong>{preview.resumen.transformaciones}</strong>
                <small>Reachable transformations</small>
              </article>
              <article>
                <span>RELACIONES</span>
                <strong>{preview.resumen.relaciones}</strong>
                <small>{preview.resumen.hops} internal hops</small>
              </article>
            </section>

            <section className="warehouse-output-card">
              <div className="warehouse-output-heading">
                <div>
                  <span>SALIDAS DWH</span>
                  <h2>Tablas de dimensiones y hechos</h2>
                  <p>
                    Tables written by the selected process and its
                    reachable transformations.
                  </p>
                </div>

                {preview.tablas_dwh.disponible && (
                  <div className="warehouse-total-badge">
                    <Database size={18} />
                    {preview.tablas_dwh.total} detected tables
                  </div>
                )}
              </div>

              {!preview.tablas_dwh.disponible ? (
                <div className="warehouse-unavailable">
                  Reconstruct the process again to include DWH table
                  detection in this analysis.
                </div>
              ) : (
                <div className="warehouse-table-groups">
                  <article className="warehouse-table-group dimensions">
                    <header>
                      <div>
                        <span>DIM</span>
                        <strong>Dimension tables</strong>
                      </div>
                      <b>{preview.tablas_dwh.total_dimensiones}</b>
                    </header>

                    <div className="warehouse-table-list">
                      {preview.tablas_dwh.dimensiones.map((table) => {
                        const evidence = table.detecciones[0]
                        const construction =
                          preview.construccion_dimensiones?.dimensiones.find(
                            (dimension) =>
                              (dimension.nombre_completo &&
                                dimension.nombre_completo ===
                                  table.nombre_completo) ||
                              (dimension.nombre_resuelto ||
                                dimension.nombre) ===
                                (table.nombre_resuelto || table.nombre),
                          )
                        const dimensionKey =
                          construction?.nombre_completo ||
                          construction?.nombre_resuelto ||
                          construction?.nombre ||
                          table.nombre_completo
                        const isSelected =
                          selectedDimensionConstruction != null &&
                          dimensionKey ===
                            (selectedDimensionConstruction.nombre_completo ||
                              selectedDimensionConstruction.nombre_resuelto ||
                              selectedDimensionConstruction.nombre)

                        return (
                          <button
                            type="button"
                            className={
                              isSelected
                                ? 'warehouse-table-row dimension-selectable selected'
                                : 'warehouse-table-row dimension-selectable'
                            }
                            key={`DIM-${table.nombre_completo}`}
                            onClick={() =>
                              setSelectedDimensionKey(dimensionKey)
                            }
                          >
                            <div>
                              <strong>
                                {table.nombre_resuelto || table.nombre}
                              </strong>
                              <small>
                                {evidence?.transformacion ||
                                  'Transformation not available'}
                              </small>
                            </div>
                            <span>
                              {construction
                                ? `${construction.tablas_fuente.length} SOURCES`
                                : evidence?.operacion}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </article>

                  <article className="warehouse-table-group facts">
                    <header>
                      <div>
                        <span>FACT</span>
                        <strong>Fact tables</strong>
                      </div>
                      <b>{preview.tablas_dwh.total_hechos}</b>
                    </header>

                    <div className="warehouse-table-list">
                      {preview.tablas_dwh.hechos.map((table) => {
                        const evidence = table.detecciones[0]
                        const factRelation =
                          preview.relaciones_fact_dim?.hechos.find(
                            (fact) =>
                              (fact.nombre_completo &&
                                fact.nombre_completo ===
                                  table.nombre_completo) ||
                              (fact.nombre_resuelto || fact.nombre) ===
                                (table.nombre_resuelto || table.nombre),
                          )
                        const factKey =
                          factRelation?.nombre_completo ||
                          factRelation?.nombre_resuelto ||
                          factRelation?.nombre ||
                          table.nombre_completo
                        const isSelected =
                          selectedFactRelation != null &&
                          factKey ===
                            (selectedFactRelation.nombre_completo ||
                              selectedFactRelation.nombre_resuelto ||
                              selectedFactRelation.nombre)

                        return (
                          <button
                            type="button"
                            className={
                              isSelected
                                ? 'warehouse-table-row fact-selectable selected'
                                : 'warehouse-table-row fact-selectable'
                            }
                            key={`FACT-${table.nombre_completo}`}
                            onClick={() => setSelectedFactKey(factKey)}
                          >
                            <div>
                              <strong>
                                {table.nombre_resuelto || table.nombre}
                              </strong>
                              <small>
                                {evidence?.transformacion ||
                                  'Transformation not available'}
                              </small>
                            </div>
                            <span>
                              {factRelation
                                ? `${factRelation.total_dimensiones} DIM`
                                : evidence?.operacion}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </article>
                </div>
              )}

              {preview.construccion_dimensiones?.disponible &&
                selectedDimensionConstruction && (
                  <section className="dimension-construction-result">
                    <div className="dimension-construction-heading">
                      <span>DIM SELECCIONADA</span>
                      <h3>
                        {selectedDimensionConstruction.nombre_resuelto ||
                          selectedDimensionConstruction.nombre}
                      </h3>
                      <p>
                        Inputs and ETL operations used to build this
                        dimension.
                      </p>
                    </div>

                    <div className="dimension-construction-details">
                      <article>
                        <strong>Source tables</strong>
                        <div className="dimension-detail-items">
                          {selectedDimensionConstruction.tablas_fuente.length >
                          0 ? (
                            selectedDimensionConstruction.tablas_fuente.map(
                              (table) => (
                                <span key={`${table.esquema}.${table.nombre}`}>
                                  <Database size={15} />
                                  {table.nombre}
                                </span>
                              ),
                            )
                          ) : (
                            <small>No source table was detected.</small>
                          )}
                        </div>
                      </article>

                      <article>
                        <strong>Variables used</strong>
                        <div className="dimension-detail-items">
                          {selectedDimensionConstruction.variables.length >
                          0 ? (
                            selectedDimensionConstruction.variables.map(
                              (variable) => (
                                <span key={variable}>${`{${variable}}`}</span>
                              ),
                            )
                          ) : (
                            <small>No variables were detected.</small>
                          )}
                        </div>
                      </article>

                      <article>
                        <strong>ETL flow</strong>
                        <div className="dimension-flow-list">
                          {selectedDimensionConstruction.pasos.map(
                            (step, index) => (
                              <span key={`${step.nombre}-${index}`}>
                                <b>{index + 1}</b>
                                {step.nombre}
                              </span>
                            ),
                          )}
                        </div>
                      </article>

                      {selectedDimensionConstruction
                        .transformaciones_auxiliares.length > 0 && (
                        <article>
                          <strong>Auxiliary transformations</strong>
                          <div className="dimension-detail-items">
                            {selectedDimensionConstruction.transformaciones_auxiliares.map(
                              (transformation) => (
                                <span key={transformation.nombre}>
                                  <Workflow size={15} />
                                  {transformation.nombre}
                                </span>
                              ),
                            )}
                          </div>
                        </article>
                      )}
                    </div>
                  </section>
                )}

              {preview.tablas_dwh.disponible &&
                !preview.construccion_dimensiones?.disponible && (
                  <div className="warehouse-unavailable fact-dimension-warning">
                    Reconstruct the process again to analyze how each DIM
                    table is built.
                  </div>
                )}

              {preview.relaciones_fact_dim?.disponible &&
                selectedFactRelation && (
                  <section className="fact-dimension-result">
                    <div className="fact-dimension-heading">
                      <span>FACT SELECCIONADA</span>
                      <h3>
                        {selectedFactRelation.nombre_resuelto ||
                          selectedFactRelation.nombre}
                      </h3>
                      <p>
                        Dimensions and columns detected in this fact table.
                      </p>
                    </div>

                    <div className="fact-detail-sections">
                      <article>
                        <header>
                          <strong>Participating dimensions</strong>
                          <span>
                            {selectedFactRelation.total_dimensiones}
                          </span>
                        </header>

                        {selectedFactRelation.dimensiones.length > 0 ? (
                          <div className="participating-dimensions">
                            {selectedFactRelation.dimensiones.map(
                              (dimension) => (
                                <div
                                  className="participating-dimension"
                                  key={
                                    dimension.nombre_resuelto ||
                                    dimension.nombre
                                  }
                                >
                                  <Database size={17} />
                                  <span>
                                    {dimension.nombre_resuelto ||
                                      dimension.nombre}
                                  </span>
                                </div>
                              ),
                            )}
                          </div>
                        ) : (
                          <div className="fact-dimension-empty">
                            No connected dimension tables were detected.
                          </div>
                        )}
                      </article>

                      <article>
                        <header>
                          <strong>FACT fields and origin</strong>
                          <span>{selectedFactRelation.total_campos}</span>
                        </header>

                        {selectedFactRelation.linaje_campos?.length > 0 ? (
                          <div className="fact-field-lineage-list">
                            {selectedFactRelation.linaje_campos.map(
                              (field) => (
                                <div
                                  className="fact-field-lineage-row"
                                  key={field.campo_fact}
                                >
                                  <div className="fact-field-name">
                                    <strong>{field.campo_fact}</strong>
                                    <span>{field.tipo_origen}</span>
                                  </div>

                                  <p>{field.detalle}</p>

                                  {field.tablas_origen.length > 0 && (
                                    <div className="fact-field-sources">
                                      {field.tablas_origen.map((table) => (
                                        <small key={table}>
                                          <Database size={13} />
                                          {table}
                                        </small>
                                      ))}
                                    </div>
                                  )}

                                  {field.fuentes_origen?.length > 0 && (
                                    <div className="fact-field-sources external">
                                      {field.fuentes_origen.map((source) => (
                                        <small key={source}>
                                          <FileText size={13} />
                                          {source}
                                        </small>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ),
                            )}
                          </div>
                        ) : (
                          <div className="fact-dimension-empty">
                            Reconstruct the process again to trace the origin
                            of each FACT field.
                          </div>
                        )}
                      </article>
                    </div>
                  </section>
                )}

              {preview.tablas_dwh.disponible &&
                !preview.relaciones_fact_dim?.disponible && (
                  <div className="warehouse-unavailable fact-dimension-warning">
                    Reconstruct the process again to identify the dimensions
                    used by each FACT table.
                  </div>
                )}
            </section>

            <section className="documentation-storage-card">
              <div>
                <h2>Información almacenada</h2>
                <p>
                  PostgreSQL stores the process architecture, DWH outputs
                  and lineage metadata needed to reopen this documentation.
                </p>
              </div>

              <div className="documentation-scope-columns">
                <div>
                  <span>INCLUIDO AHORA</span>
                  {preview.almacenamiento.incluye.map((item) => (
                    <small key={item}>
                      <CheckCircle2 size={15} />
                      {item}
                    </small>
                  ))}
                </div>

                <div className="later">
                  <span>NO ALMACENADO POR SEGURIDAD</span>
                  {preview.almacenamiento.excluye_por_ahora.map((item) => (
                    <small key={item}>{item}</small>
                  ))}
                </div>
              </div>
            </section>

            <section className="documentation-process-card">
              <div className="documentation-process-heading">
                <div>
                  <h2>Inventario de procesos</h2>
                  <p>
                    Jobs and transformations included under the selected
                    root process.
                  </p>
                </div>

                <div className="documentation-filters">
                  <label>
                    <Search size={17} />
                    <input
                      type="search"
                      placeholder="Search processes..."
                      value={searchText}
                      onChange={(event) => {
                        setSearchText(event.currentTarget.value)
                      }}
                    />
                  </label>

                  <select
                    value={typeFilter}
                    onChange={(event) => {
                      setTypeFilter(
                        event.currentTarget.value as
                          | 'all'
                          | 'JOB'
                          | 'TRANSFORMATION',
                      )
                    }}
                  >
                    <option value="all">All types</option>
                    <option value="JOB">Jobs</option>
                    <option value="TRANSFORMATION">Transformations</option>
                  </select>
                </div>
              </div>

              <div className="documentation-table-head">
                <span>Process</span>
                <span>Type</span>
                <span>Folder</span>
                <span>Diagram</span>
              </div>

              <div className="documentation-process-list">
                {filteredProcesses.map((process) => (
                  <article
                    key={process.id}
                    className={
                      process.es_principal
                        ? 'documentation-process-row root'
                        : 'documentation-process-row'
                    }
                  >
                    <div className="documentation-process-name">
                      <span>
                        {process.tipo === 'JOB' ? (
                          <Workflow size={18} />
                        ) : (
                          <Network size={18} />
                        )}
                      </span>
                      <strong>{process.nombre}</strong>
                      {process.es_principal && <small>PRINCIPAL</small>}
                    </div>

                    <span
                      className={
                        process.tipo === 'JOB'
                          ? 'documentation-type job'
                          : 'documentation-type transformation'
                      }
                    >
                      {process.tipo}
                    </span>

                    <span className="documentation-process-path">
                      {process.ruta}
                    </span>

                    <span className="documentation-diagram-count">
                      {process.elementos} elements · {process.hops} hops
                    </span>
                  </article>
                ))}
              </div>

              <footer>
                Showing {filteredProcesses.length} of{' '}
                {preview.procesos.length} processes
              </footer>
            </section>

            <section className="documentation-save-state">
              <div className="documentation-save-heading">
                <Database size={24} />
                <div>
                  <strong>
                    {savedDocumentation
                      ? 'Process documented'
                      : 'Save process documentation'}
                  </strong>
                  <p>
                    {savedDocumentation
                      ? 'This process is now permanently available without reloading the XML.'
                      : 'Choose the functional process name. It is independent from the root Job name.'}
                  </p>
                </div>
              </div>

              {savedDocumentation || preview.guardado ? (
                <div className="documentation-saved-summary">
                  <CheckCircle2 size={20} />
                  <div>
                    <strong>
                      {savedDocumentation?.nombre ||
                        preview.proceso_principal.nombre}
                    </strong>
                    <small>
                      {savedDocumentation
                        ? `${savedDocumentation.guardado.componentes} processes · ${savedDocumentation.guardado.diagramas} diagrams · ${savedDocumentation.guardado.dimensiones} DIM · ${savedDocumentation.guardado.hechos} FACT · ${savedDocumentation.guardado.variables} variables · ${savedDocumentation.guardado.parametros} parameters`
                        : `${preview.resumen.procesos} processes · ${preview.resumen.diagramas} diagrams · ${preview.tablas_dwh.total_dimensiones} DIM · ${preview.tablas_dwh.total_hechos} FACT · ${preview.linaje_persistente?.variables.length ?? 0} variables · ${preview.linaje_persistente?.parametros.length ?? 0} parameters`}
                    </small>
                  </div>
                </div>
              ) : (
                <form
                  className="documentation-save-form"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void saveDocumentation()
                  }}
                >
                  <label>
                    <span>NOMBRE DEL PROCESO</span>
                    <input
                      type="text"
                      value={processName}
                      maxLength={500}
                      placeholder="e.g. Daily transaction consolidation"
                      onChange={(event) => {
                        setProcessName(event.currentTarget.value)
                        setSaveError('')
                      }}
                    />
                  </label>

                  <button
                    type="submit"
                    disabled={processName.trim().length < 3 || isSaving}
                  >
                    {isSaving ? (
                      <LoaderCircle
                        size={18}
                        className="documentation-saving-icon"
                      />
                    ) : (
                      <Save size={18} />
                    )}
                    {isSaving ? 'Saving...' : 'Document process'}
                  </button>
                </form>
              )}

              {saveError && (
                <div className="documentation-save-error">
                  {saveError}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  )
}

export default DocumentationPage
