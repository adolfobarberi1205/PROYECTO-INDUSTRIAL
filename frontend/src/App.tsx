import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  ArrowLeft,
  Bell,
  Book,
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileText,
  GitBranch,
  Home,
  Import,
  Layers3,
  Network,
  Settings,
  Upload,
  Workflow,
} from 'lucide-react'

import './App.css'
import { API_BASE_URL } from './config/api'
import ProcessesPage from './pages/ProcessesPage'
import LineagePage from './pages/LineagePage'
import DocumentationPage from './pages/DocumentationPage'
import DiagramViewer from './pages/DiagramViewer'
import DocumentedProcessSummaryPage, {
  type DocumentedProcess,
} from './pages/DocumentedProcessSummaryPage'

type ActivePage =
  | 'overview'
  | 'processes'
  | 'documented-summary'
  | 'diagrams'
  | 'lineage'
  | 'documentation'

type RepositoryUploadResponse = {
  id_carga: string
  archivo: string
  huella_sha256: string
  repositorio_existente: RepositoryIdentity | null
  inventario?: {
    carpetas?:
      | number
      | unknown[]
      | Record<string, unknown>
    total_carpetas?: number
    folders?: number | unknown[]
  }
}

type RepositoryIdentity = {
  id: number
  nombre: string
  fecha_carga: string | null
  version_actual: number | null
  huella_actual: string | null
}

type ComplementUploadResponse = {
  id_carga: string
  kettle_cargado: boolean
  variables_encontradas: number
  scripts_cargados: number
}

type DocumentedProcessResponse = {
  total: number
  procesos: DocumentedProcess[]
}

type DocumentedRepositoryGroup = {
  id: number
  nombre: string
  procesos: DocumentedProcess[]
}

type RepositoryKettleUpdateResponse = {
  repositorio_id: number
  repositorio: string
  procesos_afectados: number
  tablas_revisadas: number
  tablas_actualizadas: number
  esquemas_resueltos: number
  nombres_resueltos: number
  variables_utilizadas: number
  variables_no_resueltas: string[]
  archivo_almacenado: boolean
  propiedades_leidas: number
}

type RepositoryKettleStatus = {
  type: 'success' | 'error'
  message: string
}

function extractFolderCount(
  response: RepositoryUploadResponse,
): number {
  const inventory = response.inventario

  if (!inventory) {
    return 0
  }

  if (
    typeof inventory.total_carpetas === 'number'
  ) {
    return inventory.total_carpetas
  }

  const folders =
    inventory.carpetas ?? inventory.folders

  if (typeof folders === 'number') {
    return folders
  }

  if (Array.isArray(folders)) {
    return folders.length
  }

  if (
    folders &&
    typeof folders === 'object'
  ) {
    return Object.keys(folders).length
  }

  return 0
}

function App() {
  const [activePage, setActivePage] =
    useState<ActivePage>(() =>
      sessionStorage.getItem('etl_opened_documented_process_id')
        ? 'documented-summary'
        : 'overview',
    )

  const [isUploadOpen, setIsUploadOpen] =
    useState(false)

  const [repositoryFile, setRepositoryFile] =
    useState<File | null>(null)

  const [kettleFile, setKettleFile] =
    useState<File | null>(null)

  const [isUploading, setIsUploading] =
    useState(false)

  const [uploadError, setUploadError] =
    useState('')

  const [uploadId, setUploadId] =
    useState<string | null>(null)

  const [repositoryName, setRepositoryName] =
    useState('')

  const [repositoryId, setRepositoryId] =
    useState<number | null>(null)

  const [repositoryNameMode, setRepositoryNameMode] =
    useState<'file' | 'manual'>('file')

  const [manualRepositoryName, setManualRepositoryName] =
    useState('')

  const [pendingRepositoryUpload, setPendingRepositoryUpload] =
    useState<{
      repository: RepositoryUploadResponse
      complement: ComplementUploadResponse | null
      requestedName: string
    } | null>(null)

  const [folderCount, setFolderCount] =
    useState(0)

  const [variablesFound, setVariablesFound] =
    useState(0)

  const [kettleLoaded, setKettleLoaded] =
    useState(false)

  const [documentedProcesses, setDocumentedProcesses] =
    useState<DocumentedProcess[]>([])

  const [isDocumentedLibraryOpen, setIsDocumentedLibraryOpen] =
    useState(false)

  const [expandedRepositoryIds, setExpandedRepositoryIds] =
    useState<number[]>([])

  const [isLoadingDocumentedProcesses, setIsLoadingDocumentedProcesses] =
    useState(false)

  const [documentedProcessesError, setDocumentedProcessesError] =
    useState('')

  const [updatingKettleRepositoryId, setUpdatingKettleRepositoryId] =
    useState<number | null>(null)

  const [repositoryKettleStatuses, setRepositoryKettleStatuses] =
    useState<Record<number, RepositoryKettleStatus>>({})

  const [selectedDocumentedProcessId, setSelectedDocumentedProcessId] =
    useState<number | null>(() => {
      const storedId = sessionStorage.getItem(
        'etl_documented_process_id',
      )

      return storedId ? Number(storedId) : null
    })

  const [openedDocumentedProcessId, setOpenedDocumentedProcessId] =
    useState<number | null>(() => {
      const storedId = sessionStorage.getItem(
        'etl_opened_documented_process_id',
      )

      return storedId ? Number(storedId) : null
    })

  const documentedRepositories = useMemo(() => {
    const repositories = new Map<number, DocumentedRepositoryGroup>()

    for (const process of documentedProcesses) {
      const repository = repositories.get(process.repositorio.id)

      if (repository) {
        repository.procesos.push(process)
        continue
      }

      repositories.set(process.repositorio.id, {
        id: process.repositorio.id,
        nombre: process.repositorio.nombre,
        procesos: [process],
      })
    }

    return Array.from(repositories.values())
      .map((repository) => ({
        ...repository,
        procesos: repository.procesos.sort((first, second) =>
          first.nombre.localeCompare(second.nombre),
        ),
      }))
      .sort((first, second) =>
        first.nombre.localeCompare(second.nombre),
      )
  }, [documentedProcesses])

  useEffect(() => {
    if (
      activePage !== 'overview' &&
      openedDocumentedProcessId === null
    ) {
      return
    }

    const controller = new AbortController()

    async function loadDocumentedProcesses() {
      setIsLoadingDocumentedProcesses(true)
      setDocumentedProcessesError('')

      try {
        const response = await fetch(
          `${API_BASE_URL}/procesos-documentados`,
          { signal: controller.signal },
        )

        if (!response.ok) {
          const data = await response.json().catch(() => null)
          throw new Error(
            data?.detail ??
              'The documented processes could not be loaded.',
          )
        }

        const data =
          (await response.json()) as DocumentedProcessResponse

        setDocumentedProcesses(data.procesos)

        setSelectedDocumentedProcessId((currentId) => {
          if (
            currentId !== null &&
            data.procesos.some((process) => process.id === currentId)
          ) {
            return currentId
          }

          sessionStorage.removeItem('etl_documented_process_id')
          return null
        })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }

        setDocumentedProcessesError(
          error instanceof Error
            ? error.message
            : 'The documented processes could not be loaded.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoadingDocumentedProcesses(false)
        }
      }
    }

    void loadDocumentedProcesses()

    return () => controller.abort()
  }, [activePage, openedDocumentedProcessId])

  function selectDocumentedProcess(processId: number) {
    setSelectedDocumentedProcessId(processId)
    sessionStorage.setItem(
      'etl_documented_process_id',
      String(processId),
    )
  }

  function toggleDocumentedRepository(repositoryId: number) {
    setExpandedRepositoryIds((current) =>
      current.includes(repositoryId)
        ? current.filter((id) => id !== repositoryId)
        : [...current, repositoryId],
    )
  }

  async function updateDocumentedRepositoryKettle(
    repositoryId: number,
    file: File,
  ) {
    if (updatingKettleRepositoryId !== null) {
      return
    }

    setUpdatingKettleRepositoryId(repositoryId)
    setRepositoryKettleStatuses((current) => {
      const next = { ...current }
      delete next[repositoryId]
      return next
    })

    try {
      const formData = new FormData()
      formData.append('kettle', file)

      const response = await fetch(
        `${API_BASE_URL}/repositorios/${repositoryId}/kettle-properties`,
        {
          method: 'POST',
          body: formData,
        },
      )

      const data = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(
          data?.detail ??
            'No se pudo actualizar la configuración Kettle.',
        )
      }

      const result = data as RepositoryKettleUpdateResponse
      const unresolvedMessage = result.variables_no_resueltas.length
        ? ` · ${result.variables_no_resueltas.length} variables sin resolver`
        : ''

      setRepositoryKettleStatuses((current) => ({
        ...current,
        [repositoryId]: {
          type: 'success',
          message:
            `${result.esquemas_resueltos} esquemas resueltos en ` +
            `${result.procesos_afectados} procesos${unresolvedMessage}.`,
        },
      }))
    } catch (error) {
      setRepositoryKettleStatuses((current) => ({
        ...current,
        [repositoryId]: {
          type: 'error',
          message:
            error instanceof Error
              ? error.message
              : 'No se pudo actualizar la configuración Kettle.',
        },
      }))
    } finally {
      setUpdatingKettleRepositoryId(null)
    }
  }

  function openDocumentedProcess(processId: number) {
    selectDocumentedProcess(processId)
    setOpenedDocumentedProcessId(processId)
    sessionStorage.setItem(
      'etl_opened_documented_process_id',
      String(processId),
    )
    setActivePage('documented-summary')
  }

  function leaveDocumentedProcess() {
    setOpenedDocumentedProcessId(null)
    setSelectedDocumentedProcessId(null)
    sessionStorage.removeItem('etl_opened_documented_process_id')
    sessionStorage.removeItem('etl_documented_process_id')
    setActivePage('overview')
  }

  function startNewAnalysis() {
    leaveDocumentedProcess()
    openRepositorySelector()
  }

  function openRepositorySelector() {
    setUploadError('')
    setPendingRepositoryUpload(null)
    setIsUploadOpen(true)
  }

  function closeRepositorySelector() {
    if (isUploading) {
      return
    }

    setIsUploadOpen(false)
  }

  async function continueRepositoryUpload() {
    if (!repositoryFile || isUploading) {
      return
    }

    setIsUploading(true)
    setUploadError('')

    const fileName = repositoryFile.name.replace(/\.xml$/i, '')
    const requestedName = (
      repositoryNameMode === 'file'
        ? fileName
        : manualRepositoryName
    ).trim()

    if (requestedName.length < 3) {
      setUploadError(
        'The repository name must contain at least 3 characters.',
      )
      setIsUploading(false)
      return
    }

    try {
      const repositoryFormData = new FormData()

      repositoryFormData.append(
        'archivo',
        repositoryFile,
      )

      const repositoryResponse = await fetch(
        `${API_BASE_URL}/repositorios/cargar`,
        {
          method: 'POST',
          body: repositoryFormData,
        },
      )

      if (!repositoryResponse.ok) {
        const errorData = await repositoryResponse
          .json()
          .catch(() => null)

        throw new Error(
          errorData?.detail ??
            'The repository could not be uploaded.',
        )
      }

      const repositoryData =
        (await repositoryResponse.json()) as
          RepositoryUploadResponse

      let complementData:
        | ComplementUploadResponse
        | null = null

      if (kettleFile) {
        const complementFormData =
          new FormData()

        complementFormData.append(
          'kettle',
          kettleFile,
        )

        const complementResponse = await fetch(
          `${API_BASE_URL}/repositorios/${repositoryData.id_carga}/complementos`,
          {
            method: 'POST',
            body: complementFormData,
          },
        )

        if (!complementResponse.ok) {
          const errorData = await complementResponse
            .json()
            .catch(() => null)

          throw new Error(
            errorData?.detail ??
              'Kettle properties could not be uploaded.',
          )
        }

        complementData =
          (await complementResponse.json()) as
            ComplementUploadResponse
      }

      if (
        repositoryData.repositorio_existente &&
        repositoryData.repositorio_existente.nombre.toLocaleLowerCase() !==
          requestedName.toLocaleLowerCase()
      ) {
        setPendingRepositoryUpload({
          repository: repositoryData,
          complement: complementData,
          requestedName,
        })
        return
      }

      completeRepositoryUpload(
        repositoryData,
        complementData,
        repositoryData.repositorio_existente?.nombre ?? requestedName,
        repositoryData.repositorio_existente?.id ?? null,
      )
    } catch (error) {
      setUploadError(
        error instanceof Error
          ? error.message
          : 'The files could not be uploaded.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  function completeRepositoryUpload(
    repositoryData: RepositoryUploadResponse,
    complementData: ComplementUploadResponse | null,
    finalName: string,
    finalRepositoryId: number | null,
  ) {
    setUploadId(repositoryData.id_carga)
    setRepositoryName(finalName)
    setRepositoryId(finalRepositoryId)
    setFolderCount(extractFolderCount(repositoryData))
    setVariablesFound(
      complementData?.variables_encontradas ?? 0,
    )
    setKettleLoaded(
      complementData?.kettle_cargado ?? false,
    )

    sessionStorage.removeItem('etl_analysis_id')
    sessionStorage.removeItem('etl_analysis_summary')
    sessionStorage.removeItem('etl_selected_folder')
    sessionStorage.removeItem('etl_selected_root_job')

    setPendingRepositoryUpload(null)
    setIsUploadOpen(false)
    setActivePage('overview')
  }

  async function resolveExistingRepository(
    rename: boolean,
  ) {
    if (!pendingRepositoryUpload || isUploading) {
      return
    }

    const existing =
      pendingRepositoryUpload.repository.repositorio_existente

    if (!existing) {
      return
    }

    setIsUploading(true)
    setUploadError('')

    try {
      let finalName = existing.nombre

      if (rename) {
        const response = await fetch(
          `${API_BASE_URL}/repositorios/${existing.id}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              nombre: pendingRepositoryUpload.requestedName,
            }),
          },
        )

        if (!response.ok) {
          const data = await response.json().catch(() => null)
          throw new Error(
            data?.detail ?? 'The repository could not be renamed.',
          )
        }

        const updated = (await response.json()) as RepositoryIdentity
        finalName = updated.nombre
      }

      completeRepositoryUpload(
        pendingRepositoryUpload.repository,
        pendingRepositoryUpload.complement,
        finalName,
        existing.id,
      )
    } catch (error) {
      setUploadError(
        error instanceof Error
          ? error.message
          : 'The repository choice could not be applied.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  const repositoryIsLoaded =
    uploadId !== null

  const selectedDocumentedProcess =
    documentedProcesses.find(
      (process) => process.id === selectedDocumentedProcessId,
    ) ?? null

  const openedDocumentedProcess =
    documentedProcesses.find(
      (process) => process.id === openedDocumentedProcessId,
    ) ?? null

  const isDocumentedContext =
    openedDocumentedProcessId !== null

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <strong>ETL Lineage</strong>
          {isDocumentedContext && (
            <small>
              {openedDocumentedProcess?.nombre ?? 'Documented process'}
            </small>
          )}
        </div>

        <nav className="top-navigation">
          {isDocumentedContext ? (
            <>
              <button
                type="button"
                className={activePage === 'documented-summary' ? 'top-nav-item active' : 'top-nav-item'}
                onClick={() => setActivePage('documented-summary')}
              >
                Summary
              </button>
              <button
                type="button"
                className={activePage === 'diagrams' ? 'top-nav-item active' : 'top-nav-item'}
                onClick={() => setActivePage('diagrams')}
              >
                Diagrams
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className={activePage === 'overview' ? 'top-nav-item active' : 'top-nav-item'}
                onClick={() => setActivePage('overview')}
              >
                Overview
              </button>
              <button
                type="button"
                className={activePage === 'processes' ? 'top-nav-item active' : 'top-nav-item'}
                onClick={() => setActivePage('processes')}
              >
                Processes
              </button>
            </>
          )}

          <button
            type="button"
            className={
              activePage === 'lineage'
                ? 'top-nav-item active'
                : 'top-nav-item'
            }
            onClick={() => {
              setActivePage('lineage')
            }}
          >
            Lineage
          </button>

          <button
            type="button"
            className={
              activePage === 'documentation'
                ? 'top-nav-item active'
                : 'top-nav-item'
            }
            onClick={() => {
              setActivePage('documentation')
            }}
          >
            Documentation
          </button>
        </nav>

        <div className="top-actions">
          {isDocumentedContext && (
            <button
              type="button"
              className="context-exit-button"
              onClick={leaveDocumentedProcess}
            >
              <ArrowLeft size={16} />
              Process catalog
            </button>
          )}

          <button
            type="button"
            className="primary-button"
            onClick={
              isDocumentedContext
                ? startNewAnalysis
                : openRepositorySelector
            }
          >
            <Upload size={17} />
            {isDocumentedContext
              ? 'New Analysis'
              : 'Upload Repository'}
          </button>

          <button
            type="button"
            className="icon-button"
            aria-label="Notifications"
          >
            <Bell size={20} />
          </button>
        </div>
      </header>

      {activePage === 'overview' && (
        <div className="workspace">
          <aside className="sidebar">
            <div>
              <div className="sidebar-title">
                ETL UI
              </div>

              <div className="sidebar-subtitle">
                PROCESS ANALYSIS
              </div>

              <nav className="side-navigation">
                <button
                  type="button"
                  className="side-nav-item active"
                  onClick={() => {
                    setActivePage('overview')
                  }}
                >
                  <Home size={19} />
                  Home
                </button>

                <button
                  type="button"
                  className="side-nav-item"
                  onClick={() => {
                    setActivePage('processes')
                  }}
                >
                  <Workflow size={19} />
                  Processes
                </button>

                <button
                  type="button"
                  className="side-nav-item"
                  onClick={() => {
                    setActivePage('lineage')
                  }}
                >
                  <GitBranch size={19} />
                  Lineage
                </button>

                <button
                  type="button"
                  className="side-nav-item"
                  onClick={() => {
                    setActivePage('documentation')
                  }}
                >
                  <FileText size={19} />
                  Documentation
                </button>
              </nav>
            </div>

            <div className="sidebar-footer">
              <button
                type="button"
                className="import-button"
                onClick={openRepositorySelector}
              >
                <Import size={17} />
                Import Process
              </button>

              <div className="sidebar-tools">
                <Upload size={17} />
                <Settings size={17} />
              </div>
            </div>
          </aside>

          <main className="main-content">
            <section className="hero-card">
              <div className="section-label">
                PROCESS ARCHITECTURE
              </div>

              <h1>
                {repositoryIsLoaded
                  ? repositoryName
                  : 'No repository uploaded'}
              </h1>

              <p className="hero-description">
                {repositoryIsLoaded ? (
                  <>
                    Repository loaded successfully.
                    Select the Processes section to
                    explore folders, Jobs and
                    transformations.
                  </>
                ) : (
                  <>
                    Explore process dependencies,
                    parameter flows and transformations
                    across your ETL architecture.
                  </>
                )}
              </p>

              <div className="metric-grid">
                <article className="metric-card">
                  <span>
                    TOTAL FOLDERS
                  </span>

                  <strong>
                    {repositoryIsLoaded
                      ? folderCount
                      : 0}
                  </strong>

                  <small
                    className={
                      repositoryIsLoaded
                        ? 'positive'
                        : ''
                    }
                  >
                    {repositoryIsLoaded
                      ? 'Repository structure loaded'
                      : 'Waiting for repository'}
                  </small>
                </article>

                <article className="metric-card">
                  <span>
                    VARIABLES FOUND
                  </span>

                  <strong>
                    {repositoryIsLoaded
                      ? variablesFound
                      : 0}
                  </strong>

                  <small>
                    {kettleLoaded
                      ? 'Loaded from kettle.properties'
                      : 'Kettle properties not loaded'}
                  </small>
                </article>
              </div>
            </section>

            <section className="process-card">
              <div className="section-heading">
                <h2>
                  Process Overview
                </h2>

                <span className="section-chip">
                  CURRENT PROCESS
                </span>
              </div>

              <div className="process-list">
                <div
                  className={
                    selectedDocumentedProcess
                      ? 'process-row selected'
                      : 'process-row'
                  }
                >
                  <span className="process-icon">
                    <Workflow size={21} />
                  </span>

                  <span className="process-name">
                    <strong>
                      {selectedDocumentedProcess?.nombre ??
                        'No documented process selected'}
                    </strong>
                    <small>
                      {selectedDocumentedProcess
                        ? `ROOT · ${selectedDocumentedProcess.job_principal.nombre}`
                        : 'SELECT ONE FROM DOCUMENTED PROCESSES'}
                    </small>
                  </span>

                  <span className="process-detail">
                    <span>
                      {selectedDocumentedProcess
                        ? `${selectedDocumentedProcess.resumen.dimensiones} DIM · ${selectedDocumentedProcess.resumen.hechos} FACT · ${selectedDocumentedProcess.resumen.variables} variables`
                        : 'Waiting for selection'}
                    </span>
                    <small
                      className={
                        selectedDocumentedProcess ? 'success' : ''
                      }
                    >
                      {selectedDocumentedProcess ? 'Selected' : 'Pending'}
                    </small>
                  </span>

                  <ChevronRight size={18} />
                </div>
              </div>
            </section>
          </main>

          <aside className="right-panel">
            <article className="flow-card">
              <div className="flow-card-title">
                <Book size={20} />

                <span>
                  PROCESOS DOCUMENTADOS
                </span>

                <b>{documentedProcesses.length}</b>
              </div>

              <button
                type="button"
                className="documented-library-trigger"
                onClick={() => setIsDocumentedLibraryOpen(true)}
                aria-label="Open documented process library"
              >
                <span className="documented-library-book">
                  <Book size={38} />
                </span>

                <span>
                  <strong>Abrir biblioteca</strong>
                  <small>
                    {documentedRepositories.length}{' '}
                    {documentedRepositories.length === 1
                      ? 'repositorio'
                      : 'repositorios'}
                  </small>
                </span>

                <ChevronRight size={18} />
              </button>
            </article>

            <div className="small-card-grid">
              <article className="small-card">
                <Layers3 size={19} />

                <span>
                  ROOT JOB
                </span>

                <strong>
                  {selectedDocumentedProcess?.job_principal.nombre ??
                    'Not selected'}
                </strong>
              </article>

              <article className="small-card">
                <Activity size={19} />

                <span>
                  STATUS
                </span>

                <strong>
                  {selectedDocumentedProcess ? 'Ready' : 'Waiting'}
                </strong>
              </article>
            </div>

            <article className="child-card">
              <div className="avatar-group">
                <span>J</span>
                <span>T</span>
                <span>J</span>
              </div>

              <strong>
                {Math.max(
                  (selectedDocumentedProcess?.resumen.componentes ?? 1) - 1,
                  0,
                )}{' '}
                CHILD
                <br />
                PROCESSES
              </strong>
            </article>

            <article className="insight-card">
              <h3>
                Analysis Insights
              </h3>

              <p>
                Select a folder and a root Job from
                Processes to begin the ETL analysis.
              </p>

              <button
                type="button"
                onClick={() => {
                  setActivePage('processes')
                }}
              >
                VIEW ANALYSIS ↗
              </button>
            </article>

            <button
              type="button"
              className="lineage-action"
              onClick={() => {
                setActivePage('lineage')
              }}
            >
              <Network size={35} />

              <strong>
                Explore Parameter Flow
              </strong>

              <span>
                TRACE VARIABLES ACROSS PROCESSES
              </span>
            </button>
          </aside>
        </div>
      )}

      {activePage === 'processes' && (
        <div className="processes-page-wrapper">
          <ProcessesPage
            uploadId={uploadId}
            repositoryName={repositoryName}
            repositoryId={repositoryId}
            onDocument={(analysisId) => {
              setOpenedDocumentedProcessId(null)
              sessionStorage.removeItem(
                'etl_opened_documented_process_id',
              )
              sessionStorage.setItem(
                'etl_analysis_id',
                analysisId,
              )
              setActivePage('documentation')
            }}
            onBatchDocumented={() => {
              setOpenedDocumentedProcessId(null)
              sessionStorage.removeItem(
                'etl_opened_documented_process_id',
              )
              setActivePage('overview')
            }}
          />
        </div>
      )}

      {activePage === 'documented-summary' && (
        <div className="documented-page-wrapper">
          {openedDocumentedProcess ? (
            <DocumentedProcessSummaryPage
              process={openedDocumentedProcess}
              onNavigate={setActivePage}
            />
          ) : (
            <section className="documented-context-loading">
              Loading documented process...
            </section>
          )}
        </div>
      )}

      {activePage === 'diagrams' && openedDocumentedProcessId && (
        <div className="documented-page-wrapper">
          <DiagramViewer
            documentedProcessId={openedDocumentedProcessId}
            onReturnToProcesses={() => {
              setActivePage('documented-summary')
            }}
          />
        </div>
      )}

      {activePage === 'lineage' && (
        <LineagePage
          analysisId={
            isDocumentedContext
              ? null
              : sessionStorage.getItem('etl_analysis_id')
          }
          documentedProcessId={openedDocumentedProcessId}
          onNavigate={setActivePage}
        />
      )}

      {activePage === 'documentation' && (
        <DocumentationPage
          analysisId={sessionStorage.getItem(
            'etl_analysis_id',
          )}
          documentedProcessId={openedDocumentedProcessId}
          onNavigate={setActivePage}
          onUpload={
            isDocumentedContext
              ? startNewAnalysis
              : openRepositorySelector
          }
          onDocumentedProcessUpdated={(updated) => {
            setDocumentedProcesses((current) =>
              current.map((process) =>
                process.id === updated.id
                  ? {
                      ...process,
                      nombre: updated.nombre,
                      descripcion: updated.descripcion,
                    }
                  : process,
              ),
            )
          }}
        />
      )}

      {isDocumentedLibraryOpen && (
        <div className="modal-backdrop">
          <section className="documented-library-modal">
            <div className="documented-library-modal-header">
              <div>
                <span>DOCUMENTED CATALOG</span>
                <h2>Procesos documentados</h2>
                <p>
                  Selecciona un repositorio para ver sus procesos asociados.
                </p>
              </div>

              <button
                type="button"
                className="modal-close"
                onClick={() => setIsDocumentedLibraryOpen(false)}
                aria-label="Close documented process library"
              >
                ×
              </button>
            </div>

            <div className="documented-repository-list">
              {isLoadingDocumentedProcesses && (
                <div className="documented-process-message">
                  Loading repositories...
                </div>
              )}

              {!isLoadingDocumentedProcesses && documentedProcessesError && (
                <div className="documented-process-message error">
                  {documentedProcessesError}
                </div>
              )}

              {!isLoadingDocumentedProcesses &&
                !documentedProcessesError &&
                documentedRepositories.length === 0 && (
                  <div className="documented-process-message">
                    No documented repositories yet.
                  </div>
                )}

              {!isLoadingDocumentedProcesses &&
                documentedRepositories.map((repository) => {
                  const isExpanded =
                    expandedRepositoryIds.includes(repository.id)

                  return (
                    <section
                      className="documented-repository-group"
                      key={repository.id}
                    >
                      <button
                        type="button"
                        className="documented-repository-button"
                        onClick={() =>
                          toggleDocumentedRepository(repository.id)
                        }
                        aria-expanded={isExpanded}
                      >
                        {isExpanded ? (
                          <BookOpen size={21} />
                        ) : (
                          <Book size={21} />
                        )}

                        <span>
                          <strong>{repository.nombre}</strong>
                          <small>
                            {repository.procesos.length}{' '}
                            {repository.procesos.length === 1
                              ? 'proceso'
                              : 'procesos'}
                          </small>
                        </span>

                        {isExpanded ? (
                          <ChevronDown size={18} />
                        ) : (
                          <ChevronRight size={18} />
                        )}
                      </button>

                      {isExpanded && (
                        <div className="documented-repository-processes">
                          <div className="repository-kettle-update">
                            <div>
                              <strong>Configuración Kettle</strong>
                              <small>
                                Resuelve los esquemas de las tablas ya
                                documentadas. El archivo no se almacena.
                              </small>
                            </div>

                            <label
                              className={
                                updatingKettleRepositoryId === repository.id
                                  ? 'repository-kettle-button disabled'
                                  : 'repository-kettle-button'
                              }
                            >
                              <Upload size={16} />
                              <span>
                                {updatingKettleRepositoryId === repository.id
                                  ? 'Actualizando...'
                                  : 'Actualizar Kettle'}
                              </span>
                              <input
                                type="file"
                                accept=".properties,.txt"
                                disabled={
                                  updatingKettleRepositoryId !== null
                                }
                                onChange={(event) => {
                                  const file = event.target.files?.[0]
                                  event.currentTarget.value = ''

                                  if (file) {
                                    void updateDocumentedRepositoryKettle(
                                      repository.id,
                                      file,
                                    )
                                  }
                                }}
                              />
                            </label>
                          </div>

                          {repositoryKettleStatuses[repository.id] && (
                            <div
                              className={
                                `repository-kettle-status ` +
                                repositoryKettleStatuses[repository.id].type
                              }
                            >
                              {
                                repositoryKettleStatuses[repository.id]
                                  .message
                              }
                            </div>
                          )}

                          {repository.procesos.map((process) => {
                            const isSelected =
                              process.id === selectedDocumentedProcessId

                            return (
                              <button
                                type="button"
                                className={
                                  isSelected
                                    ? 'documented-process-option selected'
                                    : 'documented-process-option'
                                }
                                key={process.id}
                                onClick={() =>
                                  selectDocumentedProcess(process.id)
                                }
                                onDoubleClick={() => {
                                  setIsDocumentedLibraryOpen(false)
                                  openDocumentedProcess(process.id)
                                }}
                                title="Double-click to open process"
                              >
                                <span>
                                  <strong>{process.nombre}</strong>
                                  <small>
                                    {process.resumen.dimensiones} DIM ·{' '}
                                    {process.resumen.hechos} FACT
                                  </small>
                                </span>
                                <ChevronRight size={17} />
                              </button>
                            )
                          })}
                        </div>
                      )}
                    </section>
                  )
                })}
            </div>
          </section>
        </div>
      )}

      {isUploadOpen && (
        <div className="modal-backdrop">
          <section className="upload-modal">
            <div className="upload-modal-header">
              <div>
                <span>
                  NEW ANALYSIS
                </span>

                <h2>
                  Upload Repository
                </h2>
              </div>

              <button
                type="button"
                className="modal-close"
                onClick={closeRepositorySelector}
                disabled={isUploading}
                aria-label="Close upload window"
              >
                ×
              </button>
            </div>

            <p className="upload-description">
              Upload the Pentaho repository and its
              variable configuration.
            </p>

            <div className="upload-fields">
              <label className="upload-field">
                <span>
                  REPOSITORY XML
                </span>

                <input
                  type="file"
                  accept=".xml"
                  disabled={isUploading}
                  onChange={(event) => {
                    const selectedFile =
                      event.currentTarget
                        .files?.[0] ?? null

                    setRepositoryFile(
                      selectedFile,
                    )

                    if (selectedFile) {
                      setManualRepositoryName(
                        selectedFile.name.replace(/\.xml$/i, ''),
                      )
                    }

                    setUploadError('')
                    setPendingRepositoryUpload(null)
                  }}
                />

                <strong>
                  {repositoryFile
                    ? repositoryFile.name
                    : 'Select repository XML'}
                </strong>

                <small>
                  Required
                </small>
              </label>

              <div className="repository-name-choice">
                <span>REPOSITORY NAME</span>

                <label>
                  <input
                    type="radio"
                    name="repository-name-mode"
                    checked={repositoryNameMode === 'file'}
                    disabled={isUploading || Boolean(pendingRepositoryUpload)}
                    onChange={() => setRepositoryNameMode('file')}
                  />

                  <span>
                    <strong>Use the file name</strong>
                    <small>
                      {repositoryFile
                        ? repositoryFile.name.replace(/\.xml$/i, '')
                        : 'Select an XML file first'}
                    </small>
                  </span>
                </label>

                <label>
                  <input
                    type="radio"
                    name="repository-name-mode"
                    checked={repositoryNameMode === 'manual'}
                    disabled={isUploading || Boolean(pendingRepositoryUpload)}
                    onChange={() => setRepositoryNameMode('manual')}
                  />

                  <span>
                    <strong>Use a custom name</strong>
                    <small>Choose a clear functional name.</small>
                  </span>
                </label>

                {repositoryNameMode === 'manual' && (
                  <input
                    type="text"
                    className="repository-name-input"
                    value={manualRepositoryName}
                    maxLength={255}
                    disabled={isUploading || Boolean(pendingRepositoryUpload)}
                    placeholder="DWH Corporate Repository"
                    onChange={(event) => {
                      setManualRepositoryName(event.currentTarget.value)
                      setUploadError('')
                    }}
                  />
                )}
              </div>

              <label className="upload-field">
                <span>
                  KETTLE PROPERTIES
                </span>

                <input
                  type="file"
                  accept=".properties,.txt"
                  disabled={isUploading}
                  onChange={(event) => {
                    const selectedFile =
                      event.currentTarget
                        .files?.[0] ?? null

                    setKettleFile(
                      selectedFile,
                    )

                    setUploadError('')
                  }}
                />

                <strong>
                  {kettleFile
                    ? kettleFile.name
                    : 'Select kettle.properties'}
                </strong>

                <small>
                  Recommended
                </small>
              </label>
            </div>

            {pendingRepositoryUpload?.repository.repositorio_existente && (
              <div className="repository-match-panel">
                <span>EXISTING REPOSITORY DETECTED</span>

                <strong>
                  {
                    pendingRepositoryUpload.repository
                      .repositorio_existente.nombre
                  }
                </strong>

                <p>
                  The XML content is identical, but the selected name is{' '}
                  <b>{pendingRepositoryUpload.requestedName}</b>.
                </p>

                <div>
                  <button
                    type="button"
                    className="cancel-button"
                    disabled={isUploading}
                    onClick={() => {
                      void resolveExistingRepository(false)
                    }}
                  >
                    Use existing name
                  </button>

                  <button
                    type="button"
                    className="primary-button"
                    disabled={isUploading}
                    onClick={() => {
                      void resolveExistingRepository(true)
                    }}
                  >
                    Rename repository
                  </button>
                </div>
              </div>
            )}

            {uploadError && (
              <p className="upload-error">
                {uploadError}
              </p>
            )}

            <div className="upload-modal-actions">
              <button
                type="button"
                className="cancel-button"
                onClick={closeRepositorySelector}
                disabled={isUploading}
              >
                Cancel
              </button>

              <button
                type="button"
                className="primary-button"
                disabled={
                  !repositoryFile ||
                  isUploading ||
                  Boolean(pendingRepositoryUpload)
                }
                onClick={
                  continueRepositoryUpload
                }
              >
                {isUploading
                  ? 'Uploading...'
                  : pendingRepositoryUpload
                    ? 'Choose an option above'
                    : 'Continue'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

export default App
