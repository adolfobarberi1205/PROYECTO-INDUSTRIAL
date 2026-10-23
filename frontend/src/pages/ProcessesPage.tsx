import {
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  ListChecks,
  RefreshCw,
  Search,
  Trash2,
  Workflow,
  X,
} from 'lucide-react'

import './ProcessesPage.css'
import { API_BASE_URL } from '../config/api'
import DiagramViewer from './DiagramViewer'

type ProcessesPageProps = {
  uploadId: string | null
  repositoryName: string
  repositoryId: number | null
  onDocument: (analysisId: string) => void
  onBatchDocumented: () => void
}

type RepositoryFoldersResponse = {
  id_carga: string
  total_carpetas: number
  carpetas: string[]
}

type RepositoryProcess = {
  nombre: string
  carpeta: string
  tipo: 'job' | 'transformacion'
}

type FolderContentResponse = {
  id_carga: string
  carpeta: string
  total_procesos: number
  total_jobs: number
  total_transformaciones: number
  jobs: RepositoryProcess[]
  transformaciones: RepositoryProcess[]
}

type DocumentedRootProcess = {
  id: number
  nombre: string
  repositorio: {
    id: number
  }
  job_principal: {
    nombre: string
    carpeta: string
  }
}

type DocumentedProcessesResponse = {
  procesos: DocumentedRootProcess[]
}

type AnalysisSummary = {
  jobs: number
  transformaciones: number
  procesos: number
  relaciones: number
  parametros: number
  variables: number
  tablas_dim?: number
  tablas_fact?: number
}

type AnalysisResponse = {
  id_analisis: string
  resumen: AnalysisSummary
}

type BatchProcessSelection = {
  key: string
  carpeta: string
  nombreJob: string
  nombreProceso: string
  descripcion: string
}

type BatchAnalysisItem = {
  carpeta: string
  nombre_job: string
  nombre_proceso: string
  descripcion?: string | null
  estado: 'completado' | 'fallido'
  id_analisis?: string
  resumen?: AnalysisSummary
  error?: string
}

type BatchAnalysisResponse = {
  total: number
  completados: number
  fallidos: number
  procesos: BatchAnalysisItem[]
  xml_temporal_eliminado: boolean
}

type BatchDocumentationItem = {
  id_analisis: string
  nombre_proceso: string
  estado: 'documentado' | 'fallido'
  proceso_id?: number
  error?: string
}

type BatchDocumentationResponse = {
  total: number
  documentados: number
  fallidos: number
  procesos: BatchDocumentationItem[]
}

type BatchResult = {
  key: string
  carpeta: string
  nombreJob: string
  nombreProceso: string
  estado: 'documentado' | 'fallido'
  error?: string
}

type FolderNode = {
  name: string
  path: string
  children: FolderNode[]
}

type FolderTreeItemProps = {
  node: FolderNode
  depth: number
  selectedFolder: string
  onSelect: (folderPath: string) => void
}

function buildFolderTree(
  folderPaths: string[],
): FolderNode[] {
  const root: FolderNode = {
    name: 'Repository',
    path: '/',
    children: [],
  }

  for (const folderPath of folderPaths) {
    const parts = folderPath
      .split('/')
      .filter(Boolean)

    let currentNode = root
    let currentPath = ''

    for (const part of parts) {
      currentPath = `${currentPath}/${part}`

      let childNode = currentNode.children.find(
        (child) => child.name === part,
      )

      if (!childNode) {
        childNode = {
          name: part,
          path: currentPath,
          children: [],
        }

        currentNode.children.push(childNode)
      }

      currentNode = childNode
    }
  }

  function sortNodes(
    nodes: FolderNode[],
  ): FolderNode[] {
    return nodes
      .sort((first, second) =>
        first.name.localeCompare(second.name),
      )
      .map((node) => ({
        ...node,
        children: sortNodes(node.children),
      }))
  }

  return sortNodes(root.children)
}

function rootJobKey(
  folderPath: string,
  jobName: string,
): string {
  const normalizedFolder = folderPath
    .trim()
    .replace(/\\/g, '/')
    .replace(/\/{2,}/g, '/')
    .replace(/\/$/, '')
    .toLocaleLowerCase()

  return `${normalizedFolder}::${jobName.trim().toLocaleLowerCase()}`
}

function FolderTreeItem({
  node,
  depth,
  selectedFolder,
  onSelect,
}: FolderTreeItemProps) {
  const [isExpanded, setIsExpanded] =
    useState(depth < 1)

  const hasChildren =
    node.children.length > 0

  const isSelected =
    node.path === selectedFolder

  return (
    <div className="folder-tree-node">
      <div
        className={
          isSelected
            ? 'folder-tree-row selected'
            : 'folder-tree-row'
        }
        style={{
          paddingLeft: `${12 + depth * 20}px`,
        }}
      >
        <button
          type="button"
          className="folder-expand-button"
          onClick={() => {
            if (hasChildren) {
              setIsExpanded(
                (currentValue) => !currentValue,
              )
            }
          }}
          aria-label={
            isExpanded
              ? 'Collapse folder'
              : 'Expand folder'
          }
        >
          {hasChildren ? (
            isExpanded ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )
          ) : (
            <span className="folder-empty-arrow" />
          )}
        </button>

        <button
          type="button"
          className="folder-name-button"
          onClick={() => onSelect(node.path)}
        >
          {isExpanded ? (
            <FolderOpen size={18} />
          ) : (
            <Folder size={18} />
          )}

          <span>{node.name}</span>
        </button>
      </div>

      {hasChildren && isExpanded && (
        <div>
          {node.children.map((child) => (
            <FolderTreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedFolder={selectedFolder}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

async function readResponseError(
  response: Response,
): Promise<string> {
  const data = await response
    .json()
    .catch(() => null)

  if (
    data &&
    typeof data.detail === 'string'
  ) {
    return data.detail
  }

  return 'The request could not be completed.'
}

function ProcessesPage({
  uploadId,
  repositoryName,
  repositoryId,
  onDocument,
  onBatchDocumented,
}: ProcessesPageProps) {
  const [folders, setFolders] =
    useState<string[]>([])

  const [selectedFolder, setSelectedFolder] =
    useState('')

  const [folderContent, setFolderContent] =
    useState<FolderContentResponse | null>(
      null,
    )

  const [selectedRootJob, setSelectedRootJob] =
    useState('')

  const [documentedRootJobs, setDocumentedRootJobs] =
    useState<DocumentedRootProcess[]>([])

  const [isLoadingDocumentedRoots, setIsLoadingDocumentedRoots] =
    useState(false)

  const [rootJobNotice, setRootJobNotice] =
    useState('')

  const [batchSelections, setBatchSelections] =
    useState<BatchProcessSelection[]>([])

  const [isBatchSelectorOpen, setIsBatchSelectorOpen] =
    useState(false)

  const [isBatchEditorOpen, setIsBatchEditorOpen] =
    useState(false)

  const [isBatchProcessing, setIsBatchProcessing] =
    useState(false)

  const [batchResults, setBatchResults] =
    useState<BatchResult[]>([])

  const [
    isFolderSelectorOpen,
    setIsFolderSelectorOpen,
  ] = useState(false)

  const [
    isJobSelectorOpen,
    setIsJobSelectorOpen,
  ] = useState(false)

  const [
    isLoadingFolders,
    setIsLoadingFolders,
  ] = useState(false)

  const [
    isLoadingContent,
    setIsLoadingContent,
  ] = useState(false)

  const [errorMessage, setErrorMessage] =
    useState('')

  const [searchText, setSearchText] =
    useState('')

  const [typeFilter, setTypeFilter] =
    useState<
      'all' | 'job' | 'transformacion'
    >('all')

  const [
    isReconstructing,
    setIsReconstructing,
  ] = useState(false)

  const [analysisId, setAnalysisId] =
    useState(
      () =>
        sessionStorage.getItem(
          'etl_analysis_id',
        ) ?? '',
    )

  const [
    analysisSummary,
    setAnalysisSummary,
  ] = useState<AnalysisSummary | null>(
    () => {
      const storedSummary =
        sessionStorage.getItem(
          'etl_analysis_summary',
        )

      if (!storedSummary) {
        return null
      }

      try {
        return JSON.parse(
          storedSummary,
        ) as AnalysisSummary
      } catch {
        return null
      }
    },
  )

  const [
    isDiagramOpen,
    setIsDiagramOpen,
  ] = useState(
    () =>
      Boolean(
        sessionStorage.getItem(
          'etl_analysis_id',
        ),
      ),
  )

  useEffect(() => {
    setBatchSelections([])
    setBatchResults([])
    setIsBatchSelectorOpen(false)
    setIsBatchEditorOpen(false)

    if (!uploadId) {
      setFolders([])
      setSelectedFolder('')
      setFolderContent(null)
      setSelectedRootJob('')
      return
    }

    async function loadFolders() {
      setIsLoadingFolders(true)
      setErrorMessage('')

      try {
        const response = await fetch(
          `${API_BASE_URL}/repositorios/${uploadId}/carpetas`,
        )

        if (!response.ok) {
          throw new Error(
            await readResponseError(response),
          )
        }

        const data =
          (await response.json()) as
            RepositoryFoldersResponse

        setFolders(data.carpetas)
      } catch (error) {
        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'The repository folders could not be loaded.',
        )
      } finally {
        setIsLoadingFolders(false)
      }
    }

    void loadFolders()
  }, [uploadId])

  useEffect(() => {
    setDocumentedRootJobs([])
    setRootJobNotice('')
    setIsLoadingDocumentedRoots(false)

    if (!repositoryId) {
      return
    }

    const controller = new AbortController()

    async function loadDocumentedRootJobs() {
      setIsLoadingDocumentedRoots(true)

      try {
        const response = await fetch(
          `${API_BASE_URL}/procesos-documentados`,
          { signal: controller.signal },
        )

        if (!response.ok) {
          throw new Error(
            await readResponseError(response),
          )
        }

        const data =
          (await response.json()) as DocumentedProcessesResponse

        setDocumentedRootJobs(
          data.procesos.filter(
            (process) =>
              process.repositorio.id === repositoryId,
          ),
        )
      } catch {
        if (controller.signal.aborted) {
          return
        }

        setRootJobNotice(
          'The documented root Jobs could not be verified. Try again before selecting a root Job.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoadingDocumentedRoots(false)
        }
      }
    }

    void loadDocumentedRootJobs()

    return () => controller.abort()
  }, [repositoryId])

  function clearStoredAnalysis() {
    setAnalysisId('')
    setAnalysisSummary(null)
    setIsDiagramOpen(false)
    sessionStorage.removeItem(
      'etl_analysis_id',
    )
    sessionStorage.removeItem(
      'etl_analysis_summary',
    )
    sessionStorage.removeItem(
      'etl_selected_folder',
    )
    sessionStorage.removeItem(
      'etl_selected_root_job',
    )
  }

  async function selectFolder(
    folderPath: string,
  ) {
    if (!uploadId) {
      return
    }

    setSelectedFolder(folderPath)
    setSelectedRootJob('')
    clearStoredAnalysis()
    setFolderContent(null)
    setIsFolderSelectorOpen(false)
    setIsLoadingContent(true)
    setErrorMessage('')

    try {
      const searchParameters =
        new URLSearchParams({
          carpeta: folderPath,
        })

      const response = await fetch(
        `${API_BASE_URL}/repositorios/${uploadId}/contenido?${searchParameters.toString()}`,
      )

      if (!response.ok) {
        throw new Error(
          await readResponseError(response),
        )
      }

      const data =
        (await response.json()) as
          FolderContentResponse

      setFolderContent(data)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'The folder content could not be loaded.',
      )
    } finally {
      setIsLoadingContent(false)
    }
  }

  function findDocumentedRootProcess(
    job: RepositoryProcess,
  ) {
    const key = rootJobKey(
      job.carpeta,
      job.nombre,
    )

    return documentedRootJobs.find(
      (process) =>
        rootJobKey(
          process.job_principal.carpeta,
          process.job_principal.nombre,
        ) === key,
    )
  }

  function canSelectRootJob(
    job: RepositoryProcess,
  ): boolean {
    if (repositoryId && isLoadingDocumentedRoots) {
      setRootJobNotice(
        'Checking whether this Job already belongs to a documented process. Please wait.',
      )
      return false
    }

    const documentedProcess =
      findDocumentedRootProcess(job)

    if (documentedProcess) {
      setRootJobNotice(
        `The Job '${job.nombre}' is already the root of documented process '${documentedProcess.nombre}'. Select another Job.`,
      )
      return false
    }

    if (
      repositoryId &&
      rootJobNotice.startsWith(
        'The documented root Jobs could not be verified.',
      )
    ) {
      return false
    }

    setRootJobNotice('')
    return true
  }

  function selectRootJob(
    job: RepositoryProcess,
  ): boolean {
    if (!canSelectRootJob(job)) {
      return false
    }

    setSelectedRootJob(job.nombre)
    clearStoredAnalysis()
    return true
  }

  function batchSelectionKey(
    folderPath: string,
    jobName: string,
  ) {
    return `${folderPath}::${jobName}`
  }

  function toggleBatchJob(
    job: RepositoryProcess,
  ) {
    if (!canSelectRootJob(job)) {
      return
    }

    const key = batchSelectionKey(
      job.carpeta,
      job.nombre,
    )

    setBatchSelections((current) => {
      const alreadySelected = current.some(
        (item) => item.key === key,
      )

      if (alreadySelected) {
        return current.filter(
          (item) => item.key !== key,
        )
      }

      return [
        ...current,
        {
          key,
          carpeta: job.carpeta,
          nombreJob: job.nombre,
          nombreProceso: job.nombre,
          descripcion: '',
        },
      ]
    })

    setBatchResults([])
  }

  function updateBatchSelection(
    key: string,
    changes: Partial<BatchProcessSelection>,
  ) {
    setBatchSelections((current) =>
      current.map((item) =>
        item.key === key
          ? { ...item, ...changes }
          : item,
      ),
    )
  }

  function removeBatchSelection(key: string) {
    setBatchSelections((current) =>
      current.filter((item) => item.key !== key),
    )
    setBatchResults([])
  }

  async function analyzeAndDocumentBatch() {
    if (
      !uploadId ||
      !repositoryName.trim() ||
      batchSelections.length === 0 ||
      isBatchProcessing
    ) {
      return
    }

    const invalidName = batchSelections.find(
      (item) => item.nombreProceso.trim().length < 3,
    )

    if (invalidName) {
      setErrorMessage(
        'Every process needs a name of at least 3 characters.',
      )
      return
    }

    const normalizedNames = batchSelections.map(
      (item) => item.nombreProceso.trim().toLowerCase(),
    )

    if (new Set(normalizedNames).size !== normalizedNames.length) {
      setErrorMessage(
        'Each process in the batch must have a different name.',
      )
      return
    }

    setIsBatchProcessing(true)
    setBatchResults([])
    setErrorMessage('')

    try {
      const analysisResponse = await fetch(
        `${API_BASE_URL}/repositorios/${uploadId}/analizar-lote`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            nombre_repositorio: repositoryName.trim(),
            repositorio_id: repositoryId,
            procesos: batchSelections.map((item) => ({
              carpeta: item.carpeta,
              nombre_job: item.nombreJob,
              nombre_proceso: item.nombreProceso.trim(),
              descripcion: item.descripcion.trim() || null,
            })),
          }),
        },
      )

      if (!analysisResponse.ok) {
        throw new Error(
          await readResponseError(analysisResponse),
        )
      }

      const analysisData =
        (await analysisResponse.json()) as BatchAnalysisResponse

      const completedAnalyses = analysisData.procesos.filter(
        (item) =>
          item.estado === 'completado' &&
          Boolean(item.id_analisis),
      )

      let documentationData: BatchDocumentationResponse = {
        total: 0,
        documentados: 0,
        fallidos: 0,
        procesos: [],
      }

      if (completedAnalyses.length > 0) {
        const documentationResponse = await fetch(
          `${API_BASE_URL}/analisis/documentacion/lote`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              procesos: completedAnalyses.map((item) => ({
                id_analisis: item.id_analisis,
                nombre_proceso: item.nombre_proceso,
                descripcion: item.descripcion ?? null,
              })),
            }),
          },
        )

        if (!documentationResponse.ok) {
          throw new Error(
            await readResponseError(documentationResponse),
          )
        }

        documentationData =
          (await documentationResponse.json()) as
            BatchDocumentationResponse
      }

      const results = batchSelections.map((selection) => {
        const analysis = analysisData.procesos.find(
          (item) =>
            item.carpeta === selection.carpeta &&
            item.nombre_job === selection.nombreJob,
        )

        if (!analysis || analysis.estado === 'fallido') {
          return {
            key: selection.key,
            carpeta: selection.carpeta,
            nombreJob: selection.nombreJob,
            nombreProceso: selection.nombreProceso,
            estado: 'fallido' as const,
            error:
              analysis?.error ??
              'The process could not be reconstructed.',
          }
        }

        const documentation = documentationData.procesos.find(
          (item) => item.id_analisis === analysis.id_analisis,
        )

        if (!documentation || documentation.estado === 'fallido') {
          return {
            key: selection.key,
            carpeta: selection.carpeta,
            nombreJob: selection.nombreJob,
            nombreProceso: selection.nombreProceso,
            estado: 'fallido' as const,
            error:
              documentation?.error ??
              'The process could not be documented.',
          }
        }

        return {
          key: selection.key,
          carpeta: selection.carpeta,
          nombreJob: selection.nombreJob,
          nombreProceso: selection.nombreProceso,
          estado: 'documentado' as const,
        }
      })

      setBatchResults(results)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'The process batch could not be completed.',
      )
    } finally {
      setIsBatchProcessing(false)
    }
  }

  async function reconstructSelectedProcess() {
    if (
      !uploadId ||
      !selectedFolder ||
      !selectedRootJob ||
      isReconstructing
    ) {
      return
    }

    setIsReconstructing(true)
    setErrorMessage('')

    try {
      const response = await fetch(
        `${API_BASE_URL}/repositorios/${uploadId}/analizar`,
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            carpeta: selectedFolder,
            nombre_job: selectedRootJob,
            nombre_repositorio: repositoryName,
            repositorio_id: repositoryId,
          }),
        },
      )

      if (!response.ok) {
        throw new Error(
          await readResponseError(response),
        )
      }

      const data =
        (await response.json()) as
          AnalysisResponse

      setAnalysisId(data.id_analisis)
      setAnalysisSummary(data.resumen)
      setIsDiagramOpen(true)

      sessionStorage.setItem(
        'etl_analysis_id',
        data.id_analisis,
      )

      sessionStorage.setItem(
        'etl_analysis_summary',
        JSON.stringify(data.resumen),
      )

      sessionStorage.setItem(
        'etl_selected_folder',
        selectedFolder,
      )

      sessionStorage.setItem(
        'etl_selected_root_job',
        selectedRootJob,
      )
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'The process could not be reconstructed.',
      )
    } finally {
      setIsReconstructing(false)
    }
  }

  const folderTree = useMemo(
    () => buildFolderTree(folders),
    [folders],
  )

  const allProcesses = useMemo(() => {
    if (!folderContent) {
      return []
    }

    return [
      ...folderContent.jobs,
      ...folderContent.transformaciones,
    ]
  }, [folderContent])

  const filteredProcesses = useMemo(() => {
    const normalizedSearch =
      searchText.trim().toLowerCase()

    return allProcesses.filter((process) => {
      const matchesSearch =
        !normalizedSearch ||
        process.nombre
          .toLowerCase()
          .includes(normalizedSearch)

      const matchesType =
        typeFilter === 'all' ||
        process.tipo === typeFilter

      return matchesSearch && matchesType
    })
  }, [
    allProcesses,
    searchText,
    typeFilter,
  ])

  const selectedFolderName =
    selectedFolder
      .split('/')
      .filter(Boolean)
      .pop() ?? 'Not selected'

  const totalProcesses =
    folderContent?.total_procesos ?? 0

  const totalTransformations =
    folderContent?.total_transformaciones ?? 0

  const totalJobs =
    folderContent?.total_jobs ?? 0

  return (
    <div className="processes-workspace">
      <aside className="process-actions-panel">
        <div>
          <div className="process-actions-title">
            ETL UI
          </div>

          <div className="process-actions-subtitle">
            ANÁLISIS DE PROCESOS
          </div>

          <div className="process-action-list">
            <button
              type="button"
              className="process-action-button active"
              disabled={
                !uploadId ||
                isReconstructing ||
                Boolean(analysisId)
              }
              onClick={() => {
                setIsFolderSelectorOpen(true)
              }}
            >
              <FolderOpen size={19} />

              <span>
                Select Folder
              </span>
            </button>

            <button
              type="button"
              className="process-action-button"
              disabled={
                !folderContent ||
                folderContent.jobs.length === 0 ||
                isReconstructing ||
                Boolean(analysisId)
              }
              onClick={() => {
                setIsJobSelectorOpen(true)
              }}
            >
              <Workflow size={19} />

              <span>
                Select Root Job
              </span>
            </button>

            <button
              type="button"
              className="process-action-button"
              disabled={
                (!folderContent && batchSelections.length === 0) ||
                isReconstructing ||
                isBatchProcessing ||
                Boolean(analysisId)
              }
              onClick={() => {
                if (batchSelections.length > 0) {
                  setIsBatchEditorOpen(true)
                } else {
                  setIsBatchSelectorOpen(true)
                }
              }}
            >
              <ListChecks size={19} />

              <span>
                {batchSelections.length > 0
                  ? `Batch (${batchSelections.length})`
                  : 'Select Multiple Jobs'}
              </span>
            </button>

            <button
              type="button"
              className="process-action-button"
              disabled={
                !selectedRootJob ||
                isReconstructing
              }
              onClick={() => {
                if (analysisId) {
                  setIsDiagramOpen(true)
                  return
                }

                void reconstructSelectedProcess()
              }}
            >
              <RefreshCw
                size={19}
                className={
                  isReconstructing
                    ? 'reconstructing-icon'
                    : ''
                }
              />

              <span>
                {isReconstructing
                  ? 'Reconstructing...'
                  : analysisId
                    ? 'View Diagram'
                    : 'Reconstruct Process'}
              </span>
            </button>

            <button
              type="button"
              className="process-action-button"
              disabled={!analysisId}
              onClick={() => {
                if (analysisId) {
                  onDocument(analysisId)
                }
              }}
            >
              <FileText size={19} />

              <span>
                Document
              </span>
            </button>
          </div>
        </div>

        <div>
          <div className="selected-process-summary">
            <span>
              {batchSelections.length > 0
                ? 'JOBS PRINCIPALES DEL LOTE'
                : 'JOB PRINCIPAL SELECCIONADO'}
            </span>

            <strong>
              {batchSelections.length > 0
                ? `${batchSelections.length} selected processes`
                : selectedRootJob || 'Not selected'}
            </strong>

            {batchSelections.length > 0 && (
              <small>
                Add Jobs from other folders or configure the
                batch when the selection is complete.
              </small>
            )}
          </div>

          {analysisId && analysisSummary && (
            <div className="reconstruction-result">
              <span>
                RECONSTRUCTION COMPLETED
              </span>

              <strong>
                {analysisSummary.procesos} processes
              </strong>

              <small>
                {analysisSummary.jobs} Jobs ·{' '}
                {
                  analysisSummary.transformaciones
                }{' '}
                transformations
              </small>

              <small>
                {analysisSummary.variables} variables ·{' '}
                {analysisSummary.parametros} parameters
              </small>

              <small>
                {analysisSummary.relaciones}{' '}
                relationships
              </small>

              {typeof analysisSummary.tablas_dim === 'number' &&
                typeof analysisSummary.tablas_fact === 'number' && (
                  <small>
                    {analysisSummary.tablas_dim} DIM tables ·{' '}
                    {analysisSummary.tablas_fact} FACT tables
                  </small>
                )}
            </div>
          )}
        </div>
      </aside>

      <main className="processes-content">
        {analysisId && isDiagramOpen ? (
          <DiagramViewer
            analysisId={analysisId}
            onReturnToProcesses={() => {
              setIsDiagramOpen(false)
            }}
          />
        ) : (
          <>
        <section className="process-metrics-grid">
          <article className="process-stat-card">
            <div className="process-stat-heading">
              <span>TOTAL DE PROCESOS</span>
              <GitBranch size={19} />
            </div>

            <strong>{totalProcesses}</strong>

            <small>
              In selected folder
            </small>
          </article>

          <article className="process-stat-card transformations">
            <div className="process-stat-heading">
              <span>TRANSFORMACIONES</span>
              <CheckCircle2 size={19} />
            </div>

            <strong>
              {totalTransformations}
            </strong>

            <small>
              Available transformations
            </small>
          </article>

          <article className="process-stat-card jobs">
            <div className="process-stat-heading">
              <span>JOBS</span>
              <Workflow size={19} />
            </div>

            <strong>{totalJobs}</strong>

            <small>
              Available Jobs
            </small>
          </article>

          <article className="process-stat-card selected-folder">
            <div className="process-stat-heading">
              <span>CARPETA SELECCIONADA</span>
              <FolderOpen size={19} />
            </div>

            <strong title={selectedFolder}>
              {selectedFolderName}
            </strong>

            <small>
              {selectedFolder ||
                'No folder selected'}
            </small>
          </article>
        </section>

        <section className="process-browser-card">
          <div className="process-browser-header">
            <div>
              <h2>Todos los procesos</h2>

              <p>
                Jobs and transformations from the
                selected repository folder.
              </p>
            </div>

            <div className="process-browser-filters">
              <label className="process-search">
                <Search size={17} />

                <input
                  type="search"
                  placeholder="Search by name..."
                  value={searchText}
                  onChange={(event) => {
                    setSearchText(
                      event.currentTarget.value,
                    )
                  }}
                />
              </label>

              <select
                value={typeFilter}
                onChange={(event) => {
                  setTypeFilter(
                    event.currentTarget.value as
                      | 'all'
                      | 'job'
                      | 'transformacion',
                  )
                }}
              >
                <option value="all">
                  All Types
                </option>

                <option value="job">
                  Jobs
                </option>

                <option value="transformacion">
                  Transformations
                </option>
              </select>
            </div>
          </div>

          {errorMessage && (
            <div className="processes-error">
              {errorMessage}
            </div>
          )}

          {rootJobNotice && (
            <div className="root-job-selection-warning">
              {rootJobNotice}
            </div>
          )}

          {!uploadId && (
            <div className="processes-empty-state">
              Upload a repository from Overview
              before opening Processes.
            </div>
          )}

          {uploadId &&
            !selectedFolder &&
            !errorMessage && (
              <div className="processes-empty-state">
                Select a repository folder to
                display its Jobs and
                transformations.
              </div>
            )}

          {isLoadingContent && (
            <div className="processes-empty-state">
              Loading folder content...
            </div>
          )}

          {folderContent &&
            !isLoadingContent && (
              <>
                <div className="process-table-header">
                  <span>Process Name</span>
                  <span>Type</span>
                  <span>Folder</span>
                  <span>Status</span>
                  <span />
                </div>

                <div className="process-table-body">
                  {filteredProcesses.map(
                    (process) => {
                      const isSelectedRootJob =
                        process.tipo === 'job' &&
                        process.nombre ===
                          selectedRootJob
                      const documentedProcess =
                        process.tipo === 'job'
                          ? findDocumentedRootProcess(
                              process,
                            )
                          : undefined

                      return (
                        <button
                          type="button"
                          className={[
                            'repository-process-row',
                            isSelectedRootJob
                              ? 'selected'
                              : '',
                            documentedProcess
                              ? 'documented-root'
                              : '',
                          ]
                            .filter(Boolean)
                            .join(' ')}
                          key={`${process.carpeta}-${process.tipo}-${process.nombre}`}
                          disabled={
                            isReconstructing ||
                            Boolean(analysisId)
                          }
                          onClick={() => {
                            if (
                              process.tipo === 'job'
                            ) {
                              selectRootJob(process)
                            }
                          }}
                        >
                          <span className="repository-process-name">
                            <span className="repository-process-icon">
                              {process.tipo ===
                              'job' ? (
                                <Workflow
                                  size={19}
                                />
                              ) : (
                                <GitBranch
                                  size={19}
                                />
                              )}
                            </span>

                            <strong>
                              {process.nombre}
                            </strong>
                          </span>

                          <span
                            className={
                              process.tipo === 'job'
                                ? 'process-type-chip job'
                                : 'process-type-chip transformation'
                            }
                          >
                            {process.tipo === 'job'
                              ? 'JOB'
                              : 'TRANSFORMATION'}
                          </span>

                          <span className="repository-folder-path">
                            {process.carpeta}
                          </span>

                          <span
                            className={
                              documentedProcess
                                ? 'process-status documented'
                                : isSelectedRootJob
                                ? 'process-status selected'
                                : 'process-status available'
                            }
                          >
                            {documentedProcess
                              ? 'Already documented'
                              : isSelectedRootJob
                              ? 'Root selected'
                              : 'Available'}
                          </span>

                          <ChevronRight size={17} />
                        </button>
                      )
                    },
                  )}
                </div>

                <div className="process-table-footer">
                  Showing{' '}
                  {filteredProcesses.length} of{' '}
                  {allProcesses.length} processes
                </div>
              </>
            )}
        </section>
          </>
        )}
      </main>

      {isFolderSelectorOpen && (
        <div className="process-selector-backdrop">
          <section className="process-selector-modal">
            <div className="process-selector-header">
              <div>
                <span>
                  REPOSITORY STRUCTURE
                </span>

                <h2>Seleccionar carpeta</h2>
              </div>

              <button
                type="button"
                onClick={() => {
                  setIsFolderSelectorOpen(false)
                }}
                aria-label="Close folder selector"
              >
                <X size={22} />
              </button>
            </div>

            <div className="folder-tree">
              {isLoadingFolders ? (
                <div className="folder-selector-message">
                  Loading repository folders...
                </div>
              ) : (
                folderTree.map((node) => (
                  <FolderTreeItem
                    key={node.path}
                    node={node}
                    depth={0}
                    selectedFolder={
                      selectedFolder
                    }
                    onSelect={selectFolder}
                  />
                ))
              )}
            </div>
          </section>
        </div>
      )}

      {isJobSelectorOpen &&
        folderContent && (
          <div className="process-selector-backdrop">
            <section className="process-selector-modal job-selector">
              <div className="process-selector-header">
                <div>
                  <span>PROCESO PRINCIPAL</span>

                  <h2>Seleccionar Job principal</h2>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setIsJobSelectorOpen(false)
                  }}
                  aria-label="Close Job selector"
                >
                  <X size={22} />
                </button>
              </div>

              {rootJobNotice && (
                <div className="root-job-selection-warning modal-warning">
                  {rootJobNotice}
                </div>
              )}

              <div className="job-selector-list">
                {folderContent.jobs.map(
                  (job) => {
                    const documentedProcess =
                      findDocumentedRootProcess(job)

                    return (
                    <button
                      type="button"
                      key={`${job.carpeta}-${job.nombre}`}
                      className={[
                        'job-selector-item',
                        selectedRootJob === job.nombre
                          ? 'selected'
                          : '',
                        documentedProcess
                          ? 'documented-root'
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      aria-disabled={Boolean(documentedProcess)}
                      onClick={() => {
                        if (selectRootJob(job)) {
                          setIsJobSelectorOpen(
                            false,
                          )
                        }
                      }}
                    >
                      <Workflow size={19} />

                      <span className="job-selector-name">
                        <strong>{job.nombre}</strong>

                        {documentedProcess && (
                          <small>
                            Already documented in{' '}
                            {documentedProcess.nombre}
                          </small>
                        )}
                      </span>

                      <ChevronRight size={17} />
                    </button>
                    )
                  },
                )}
              </div>
            </section>
          </div>
        )}

      {isBatchSelectorOpen && folderContent && (
        <div className="process-selector-backdrop">
          <section className="process-selector-modal batch-selector">
            <div className="process-selector-header">
              <div>
                <span>VARIOS PROCESOS PRINCIPALES</span>

                <h2>Seleccionar Jobs principales</h2>

                <p>
                  Select every Job that represents an independent
                  ETL process in this folder.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setIsBatchSelectorOpen(false)}
                aria-label="Close multiple Job selector"
              >
                <X size={22} />
              </button>
            </div>

            {rootJobNotice && (
              <div className="root-job-selection-warning modal-warning">
                {rootJobNotice}
              </div>
            )}

            <div className="job-selector-list">
              {folderContent.jobs.map((job) => {
                const key = batchSelectionKey(
                  job.carpeta,
                  job.nombre,
                )
                const selected = batchSelections.some(
                  (item) => item.key === key,
                )
                const documentedProcess =
                  findDocumentedRootProcess(job)

                return (
                  <button
                    type="button"
                    key={key}
                    className={[
                      'job-selector-item batch',
                      selected ? 'selected' : '',
                      documentedProcess
                        ? 'documented-root'
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    aria-disabled={Boolean(documentedProcess)}
                    onClick={() => toggleBatchJob(job)}
                  >
                    <span className="batch-check">
                      {selected ? '✓' : ''}
                    </span>

                    <span>{job.nombre}</span>

                    <small>
                      {documentedProcess
                        ? `Already documented in ${documentedProcess.nombre}`
                        : job.carpeta}
                    </small>
                  </button>
                )
              })}
            </div>

            <div className="batch-modal-footer">
              <span>
                {batchSelections.length} Jobs selected in total
              </span>

              <div>
                <button
                  type="button"
                  className="batch-secondary-button"
                  onClick={() => setIsBatchSelectorOpen(false)}
                >
                  Keep selecting later
                </button>

                <button
                  type="button"
                  className="batch-primary-button"
                  disabled={batchSelections.length === 0}
                  onClick={() => {
                    setIsBatchSelectorOpen(false)
                    setIsBatchEditorOpen(true)
                  }}
                >
                  Configure processes
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {isBatchEditorOpen && (
        <div className="process-selector-backdrop">
          <section className="process-selector-modal batch-editor">
            <div className="process-selector-header">
              <div>
                <span>DOCUMENTACIÓN POR LOTES</span>

                <h2>
                  {batchResults.length > 0
                    ? 'Resultados del lote'
                    : 'Nombra los procesos ETL'}
                </h2>

                <p>
                  {batchResults.length > 0
                    ? 'Each process was handled independently.'
                    : 'The functional name is independent from the root Job name.'}
                </p>
              </div>

              <button
                type="button"
                disabled={isBatchProcessing}
                onClick={() => setIsBatchEditorOpen(false)}
                aria-label="Close batch editor"
              >
                <X size={22} />
              </button>
            </div>

            {errorMessage && (
              <div className="processes-error batch-error">
                {errorMessage}
              </div>
            )}

            {batchResults.length === 0 ? (
              <div className="batch-process-list">
                {batchSelections.map((item, index) => (
                  <article className="batch-process-card" key={item.key}>
                    <div className="batch-process-card-heading">
                      <div>
                        <span>PROCESS {index + 1}</span>
                        <strong>{item.nombreJob}</strong>
                        <small>{item.carpeta}</small>
                      </div>

                      <button
                        type="button"
                        disabled={isBatchProcessing}
                        onClick={() => removeBatchSelection(item.key)}
                        aria-label={`Remove ${item.nombreJob}`}
                      >
                        <Trash2 size={18} />
                      </button>
                    </div>

                    <label>
                      <span>NOMBRE DEL PROCESO</span>
                      <input
                        value={item.nombreProceso}
                        maxLength={255}
                        disabled={isBatchProcessing}
                        onChange={(event) =>
                          updateBatchSelection(item.key, {
                            nombreProceso: event.currentTarget.value,
                          })
                        }
                      />
                    </label>

                    <label>
                      <span>DESCRIPTION (OPTIONAL)</span>
                      <textarea
                        value={item.descripcion}
                        maxLength={2000}
                        rows={2}
                        disabled={isBatchProcessing}
                        onChange={(event) =>
                          updateBatchSelection(item.key, {
                            descripcion: event.currentTarget.value,
                          })
                        }
                      />
                    </label>
                  </article>
                ))}

                {batchSelections.length === 0 && (
                  <div className="folder-selector-message">
                    Add at least one root Job to continue.
                  </div>
                )}
              </div>
            ) : (
              <div className="batch-results-list">
                {batchResults.map((result) => (
                  <article
                    className={`batch-result-card ${result.estado}`}
                    key={result.key}
                  >
                    <div>
                      <span>
                        {result.estado === 'documentado'
                          ? 'DOCUMENTED'
                          : 'NOT DOCUMENTED'}
                      </span>
                      <strong>{result.nombreProceso}</strong>
                      <small>
                        {result.nombreJob} · {result.carpeta}
                      </small>
                    </div>

                    {result.error && <p>{result.error}</p>}
                  </article>
                ))}
              </div>
            )}

            <div className="batch-modal-footer">
              <span>
                {batchResults.length > 0
                  ? `${batchResults.filter((item) => item.estado === 'documentado').length} of ${batchResults.length} documented`
                  : `${batchSelections.length} processes ready`}
              </span>

              <div>
                {batchResults.length === 0 ? (
                  <>
                    <button
                      type="button"
                      className="batch-secondary-button"
                      disabled={isBatchProcessing}
                      onClick={() => {
                        setIsBatchEditorOpen(false)
                        setIsBatchSelectorOpen(true)
                      }}
                    >
                      Add more Jobs
                    </button>

                    <button
                      type="button"
                      className="batch-primary-button"
                      disabled={
                        batchSelections.length === 0 ||
                        isBatchProcessing
                      }
                      onClick={() => void analyzeAndDocumentBatch()}
                    >
                      {isBatchProcessing
                        ? 'Analyzing and documenting...'
                        : 'Analyze and document all'}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      className="batch-secondary-button"
                      onClick={() => setIsBatchEditorOpen(false)}
                    >
                      Close
                    </button>

                    <button
                      type="button"
                      className="batch-primary-button"
                      onClick={onBatchDocumented}
                    >
                      View documented processes
                    </button>
                  </>
                )}
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

export default ProcessesPage
