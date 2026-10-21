import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type {
  PointerEvent as ReactPointerEvent,
} from 'react'

import {
  ArrowLeft,
  ChevronRight,
  Code2,
  Database,
  GitBranch,
  Home,
  Info,
  LoaderCircle,
  Maximize2,
  Minus,
  Minimize2,
  Plus,
  Workflow,
  X,
} from 'lucide-react'

import './DiagramViewer.css'

type DiagramTarget = {
  id: string | null
  nombre?: string
  tipo?: string
  ruta?: string
  estado?: string
  navegable: boolean
}

type DiagramElement = {
  id: string
  nombre: string
  tipo: string
  categoria: 'ENTRY' | 'STEP'
  x: number
  y: number
  habilitado: boolean
  target?: DiagramTarget | null
  navegable: boolean
}

type DiagramHop = {
  origen: string
  destino: string
  habilitado: boolean
  evaluacion?: string
  incondicional?: boolean
}

type TechnicalTable = {
  esquema: string
  nombre: string
  nombre_completo: string
}

type SqlScript = {
  paso: string
  tipo_paso: string
  sql: string
  tablas_consultadas: TechnicalTable[]
  tablas_destino: TechnicalTable[]
}

type TransformationTechnicalDetail = {
  total_pasos: number
  total_hops: number
  tipos_paso: Array<{
    tipo: string
    cantidad: number
  }>
  tablas_consultadas: TechnicalTable[]
  tablas_destino: TechnicalTable[]
  tiene_sql: boolean
  scripts_sql: SqlScript[]
  variables: string[]
  parametros: string[]
  detalle_historico_limitado?: boolean
}

type Diagram = {
  id: string
  nombre: string
  tipo: 'JOB' | 'TRANSFORMATION'
  ruta: string
  elementos: DiagramElement[]
  hops: DiagramHop[]
  detalle_tecnico?: TransformationTechnicalDetail | null
  total_elementos: number
  total_hops: number
  ancho_recomendado: number
  alto_recomendado: number
}

type DiagramResponse = {
  id_analisis: string
  diagrama_principal_id: string
  es_principal: boolean
  diagrama: Diagram
}

type DiagramViewerProps = {
  analysisId?: string | null
  documentedProcessId?: number | null
  onReturnToProcesses: () => void
}

type PositionedElement = DiagramElement & {
  displayX: number
  displayY: number
}

type NodePosition = {
  x: number
  y: number
}

type DiagramLayouts = Record<
  string,
  Record<string, NodePosition>
>

type DragState = {
  pointerId: number
  elementId: string
  startClientX: number
  startClientY: number
  startX: number
  startY: number
  moved: boolean
}

type CanvasPanState = {
  pointerId: number
  startClientX: number
  startClientY: number
  startScrollLeft: number
  startScrollTop: number
}

const NODE_WIDTH = 178
const NODE_HEIGHT = 68
const CANVAS_PADDING = 80
const NODE_EDGE_GAP = 5

async function readError(
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

  return 'The diagram could not be loaded.'
}

function calculateConnectionPoints(
  source: PositionedElement,
  target: PositionedElement,
) {
  const sourceCenterX =
    source.displayX + NODE_WIDTH / 2
  const sourceCenterY =
    source.displayY + NODE_HEIGHT / 2
  const targetCenterX =
    target.displayX + NODE_WIDTH / 2
  const targetCenterY =
    target.displayY + NODE_HEIGHT / 2

  const deltaX =
    targetCenterX - sourceCenterX
  const deltaY =
    targetCenterY - sourceCenterY

  if (deltaX === 0 && deltaY === 0) {
    return null
  }

  const sourceScale = Math.min(
    deltaX === 0
      ? Number.POSITIVE_INFINITY
      : (NODE_WIDTH / 2 +
          NODE_EDGE_GAP) /
          Math.abs(deltaX),
    deltaY === 0
      ? Number.POSITIVE_INFINITY
      : (NODE_HEIGHT / 2 +
          NODE_EDGE_GAP) /
          Math.abs(deltaY),
  )

  const targetScale = Math.min(
    deltaX === 0
      ? Number.POSITIVE_INFINITY
      : (NODE_WIDTH / 2 +
          NODE_EDGE_GAP) /
          Math.abs(deltaX),
    deltaY === 0
      ? Number.POSITIVE_INFINITY
      : (NODE_HEIGHT / 2 +
          NODE_EDGE_GAP) /
          Math.abs(deltaY),
  )

  return {
    x1:
      sourceCenterX +
      deltaX * sourceScale,
    y1:
      sourceCenterY +
      deltaY * sourceScale,
    x2:
      targetCenterX -
      deltaX * targetScale,
    y2:
      targetCenterY -
      deltaY * targetScale,
  }
}

function DiagramViewer({
  analysisId,
  documentedProcessId,
  onReturnToProcesses,
}: DiagramViewerProps) {
  const viewerRef =
    useRef<HTMLElement | null>(null)

  const scrollAreaRef =
    useRef<HTMLDivElement | null>(null)

  const dragStateRef =
    useRef<DragState | null>(null)

  const canvasPanRef =
    useRef<CanvasPanState | null>(null)

  const suppressClickRef =
    useRef(false)

  const [currentDiagram, setCurrentDiagram] =
    useState<Diagram | null>(null)

  const [mainDiagramId, setMainDiagramId] =
    useState('')

  const [history, setHistory] =
    useState<Diagram[]>([])

  const [zoom, setZoom] =
    useState(1)

  const [isLoading, setIsLoading] =
    useState(true)

  const [errorMessage, setErrorMessage] =
    useState('')

  const [isFullscreen, setIsFullscreen] =
    useState(false)

  const [isPanning, setIsPanning] =
    useState(false)

  const [isTechnicalPanelOpen, setIsTechnicalPanelOpen] =
    useState(false)

  const [
    diagramLayouts,
    setDiagramLayouts,
  ] = useState<DiagramLayouts>({})

  const loadDiagram = useCallback(
    async (
      processId?: string,
    ): Promise<DiagramResponse> => {
      const parameters =
        new URLSearchParams()

      if (processId) {
        parameters.set(
          documentedProcessId
            ? 'componente_id'
            : 'proceso_id',
          processId,
        )
      }

      const query = parameters.toString()
      const endpoint = documentedProcessId
        ? `http://127.0.0.1:8000/procesos-documentados/${documentedProcessId}/diagrama`
        : `http://127.0.0.1:8000/analisis/${analysisId}/diagrama`
      const response = await fetch(
        `${endpoint}${
          query ? `?${query}` : ''
        }`,
      )

      if (!response.ok) {
        throw new Error(
          await readError(response),
        )
      }

      return (
        (await response.json()) as
          DiagramResponse
      )
    },
    [analysisId, documentedProcessId],
  )

  useEffect(() => {
    let isActive = true

    async function loadMainDiagram() {
      setIsLoading(true)
      setErrorMessage('')

      try {
        const data = await loadDiagram()

        if (!isActive) {
          return
        }

        setMainDiagramId(
          data.diagrama_principal_id,
        )
        setCurrentDiagram(data.diagrama)
        setHistory([])
        setZoom(1)
      } catch (error) {
        if (!isActive) {
          return
        }

        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'The diagram could not be loaded.',
        )
      } finally {
        if (isActive) {
          setIsLoading(false)
        }
      }
    }

    void loadMainDiagram()

    return () => {
      isActive = false
    }
  }, [loadDiagram])

  useEffect(() => {
    function handleFullscreenChange() {
      setIsFullscreen(
        document.fullscreenElement ===
          viewerRef.current,
      )
    }

    document.addEventListener(
      'fullscreenchange',
      handleFullscreenChange,
    )

    return () => {
      document.removeEventListener(
        'fullscreenchange',
        handleFullscreenChange,
      )
    }
  }, [])

  useEffect(() => {
    setIsTechnicalPanelOpen(
      currentDiagram?.tipo === 'TRANSFORMATION',
    )
  }, [currentDiagram?.id, currentDiagram?.tipo])

  const positionedElements = useMemo<
    PositionedElement[]
  >(() => {
    if (!currentDiagram) {
      return []
    }

    const xValues =
      currentDiagram.elementos.map(
        (element) => element.x,
      )

    const yValues =
      currentDiagram.elementos.map(
        (element) => element.y,
      )

    const minX = Math.min(
      ...xValues,
      0,
    )

    const minY = Math.min(
      ...yValues,
      0,
    )

    const currentLayout =
      diagramLayouts[currentDiagram.id] ?? {}

    return currentDiagram.elementos.map(
      (element) => {
        const savedPosition =
          currentLayout[element.id]

        return {
          ...element,
          displayX:
            savedPosition?.x ??
            element.x -
              minX +
              CANVAS_PADDING,
          displayY:
            savedPosition?.y ??
            element.y -
              minY +
              CANVAS_PADDING,
        }
      },
    )
  }, [currentDiagram, diagramLayouts])

  const positionByName = useMemo(() => {
    const positions = new Map<
      string,
      PositionedElement
    >()

    for (
      const element of positionedElements
    ) {
      if (!positions.has(element.nombre)) {
        positions.set(
          element.nombre,
          element,
        )
      }
    }

    return positions
  }, [positionedElements])

  const canvasSize = useMemo(() => {
    const maxX = Math.max(
      ...positionedElements.map(
        (element) => element.displayX,
      ),
      700,
    )

    const maxY = Math.max(
      ...positionedElements.map(
        (element) => element.displayY,
      ),
      430,
    )

    return {
      width:
        maxX +
        NODE_WIDTH +
        CANVAS_PADDING,
      height:
        maxY +
        NODE_HEIGHT +
        CANVAS_PADDING,
    }
  }, [positionedElements])

  async function openTarget(
    element: DiagramElement,
  ) {
    const targetId =
      element.target?.id

    if (
      !currentDiagram ||
      !element.navegable ||
      !targetId
    ) {
      return
    }

    setIsLoading(true)
    setErrorMessage('')

    try {
      const data = await loadDiagram(
        targetId,
      )

      setHistory((currentHistory) => [
        ...currentHistory,
        currentDiagram,
      ])
      setCurrentDiagram(data.diagrama)
      setZoom(1)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'The linked process could not be opened.',
      )
    } finally {
      setIsLoading(false)
    }
  }

  function startDragging(
    event: ReactPointerEvent<HTMLButtonElement>,
    element: PositionedElement,
  ) {
    if (event.button !== 0) {
      return
    }

    dragStateRef.current = {
      pointerId: event.pointerId,
      elementId: element.id,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: element.displayX,
      startY: element.displayY,
      moved: false,
    }

    event.currentTarget.setPointerCapture(
      event.pointerId,
    )
    event.stopPropagation()
    event.preventDefault()
  }

  function moveDraggedNode(
    event: ReactPointerEvent<HTMLButtonElement>,
  ) {
    const dragState =
      dragStateRef.current

    if (
      !dragState ||
      !currentDiagram ||
      dragState.pointerId !==
        event.pointerId
    ) {
      return
    }

    const deltaX =
      (event.clientX -
        dragState.startClientX) /
      zoom
    const deltaY =
      (event.clientY -
        dragState.startClientY) /
      zoom

    if (
      Math.abs(deltaX) > 3 ||
      Math.abs(deltaY) > 3
    ) {
      dragState.moved = true
    }

    const nextPosition = {
      x: Math.max(
        20,
        dragState.startX + deltaX,
      ),
      y: Math.max(
        20,
        dragState.startY + deltaY,
      ),
    }

    setDiagramLayouts(
      (currentLayouts) => ({
        ...currentLayouts,
        [currentDiagram.id]: {
          ...currentLayouts[
            currentDiagram.id
          ],
          [dragState.elementId]:
            nextPosition,
        },
      }),
    )
  }

  function finishDragging(
    event: ReactPointerEvent<HTMLButtonElement>,
  ) {
    const dragState =
      dragStateRef.current

    if (
      !dragState ||
      dragState.pointerId !==
        event.pointerId
    ) {
      return
    }

    suppressClickRef.current =
      dragState.moved
    dragStateRef.current = null

    if (
      event.currentTarget.hasPointerCapture(
        event.pointerId,
      )
    ) {
      event.currentTarget.releasePointerCapture(
        event.pointerId,
      )
    }
  }

  async function toggleFullscreen() {
    try {
      if (
        document.fullscreenElement ===
        viewerRef.current
      ) {
        await document.exitFullscreen()
        return
      }

      await viewerRef.current?.requestFullscreen()
    } catch {
      setErrorMessage(
        'Fullscreen mode is not available in this browser.',
      )
    }
  }

  function startCanvasPan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (event.button !== 0) {
      return
    }

    const eventTarget =
      event.target as HTMLElement

    if (
      eventTarget.closest(
        '.diagram-node',
      )
    ) {
      return
    }

    const scrollArea =
      scrollAreaRef.current

    if (!scrollArea) {
      return
    }

    canvasPanRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScrollLeft:
        scrollArea.scrollLeft,
      startScrollTop:
        scrollArea.scrollTop,
    }

    scrollArea.setPointerCapture(
      event.pointerId,
    )
    setIsPanning(true)
    event.preventDefault()
  }

  function moveCanvasPan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const panState =
      canvasPanRef.current
    const scrollArea =
      scrollAreaRef.current

    if (
      !panState ||
      !scrollArea ||
      panState.pointerId !==
        event.pointerId
    ) {
      return
    }

    scrollArea.scrollLeft =
      panState.startScrollLeft -
      (event.clientX -
        panState.startClientX)

    scrollArea.scrollTop =
      panState.startScrollTop -
      (event.clientY -
        panState.startClientY)
  }

  function finishCanvasPan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const panState =
      canvasPanRef.current
    const scrollArea =
      scrollAreaRef.current

    if (
      !panState ||
      panState.pointerId !==
        event.pointerId
    ) {
      return
    }

    canvasPanRef.current = null
    setIsPanning(false)

    if (
      scrollArea?.hasPointerCapture(
        event.pointerId,
      )
    ) {
      scrollArea.releasePointerCapture(
        event.pointerId,
      )
    }
  }

  function goBack() {
    const previous =
      history.at(-1)

    if (!previous) {
      return
    }

    setHistory((currentHistory) =>
      currentHistory.slice(0, -1),
    )
    setCurrentDiagram(previous)
    setZoom(1)
  }

  async function goHome() {
    if (
      currentDiagram?.id ===
      mainDiagramId
    ) {
      return
    }

    setIsLoading(true)
    setErrorMessage('')

    try {
      const data = await loadDiagram(
        mainDiagramId,
      )

      setCurrentDiagram(data.diagrama)
      setHistory([])
      setZoom(1)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'The main diagram could not be opened.',
      )
    } finally {
      setIsLoading(false)
    }
  }

  function openBreadcrumb(
    diagram: Diagram,
    index: number,
  ) {
    setCurrentDiagram(diagram)
    setHistory((currentHistory) =>
      currentHistory.slice(0, index),
    )
    setZoom(1)
  }

  const breadcrumbs = [
    ...history,
    ...(currentDiagram
      ? [currentDiagram]
      : []),
  ]

  return (
    <section
      ref={viewerRef}
      className="diagram-viewer"
    >
      <header className="diagram-viewer-header">
        <div>
          <span className="diagram-eyebrow">
            RECONSTRUCTED PROCESS
          </span>

          <h2>
            {currentDiagram?.nombre ??
              'Process diagram'}
          </h2>

          <p>
            {currentDiagram?.ruta ||
              'Loading process information...'}
          </p>
        </div>

        <div className="diagram-header-actions">
          {currentDiagram?.tipo === 'TRANSFORMATION' && (
            <button
              type="button"
              className={
                isTechnicalPanelOpen
                  ? 'diagram-fullscreen-button active'
                  : 'diagram-fullscreen-button'
              }
              onClick={() =>
                setIsTechnicalPanelOpen((value) => !value)
              }
              aria-label="Mostrar información de la transformación"
              title="Información técnica"
            >
              <Info size={19} />
            </button>
          )}

          <button
            type="button"
            className="diagram-fullscreen-button"
            onClick={() => {
              void toggleFullscreen()
            }}
            aria-label={
              isFullscreen
                ? 'Exit fullscreen'
                : 'Open diagram in fullscreen'
            }
            title={
              isFullscreen
                ? 'Exit fullscreen'
                : 'Fullscreen'
            }
          >
            {isFullscreen ? (
              <Minimize2 size={19} />
            ) : (
              <Maximize2 size={19} />
            )}
          </button>

          <button
            type="button"
            className="diagram-secondary-button"
            onClick={
              onReturnToProcesses
            }
          >
            Process Browser
          </button>
        </div>
      </header>

      <div className="diagram-toolbar">
        <div className="diagram-navigation">
          <button
            type="button"
            disabled={
              history.length === 0
            }
            onClick={goBack}
          >
            <ArrowLeft size={17} />
            Back
          </button>

          <button
            type="button"
            disabled={
              currentDiagram?.id ===
              mainDiagramId
            }
            onClick={() => {
              void goHome()
            }}
          >
            <Home size={17} />
            Home
          </button>
        </div>

        <nav
          className="diagram-breadcrumbs"
          aria-label="Diagram path"
        >
          {breadcrumbs.map(
            (diagram, index) => (
              <span key={`${diagram.id}-${index}`}>
                {index > 0 && (
                  <span
                    className="diagram-breadcrumb-separator"
                  >
                    /
                  </span>
                )}

                <button
                  type="button"
                  className={
                    index ===
                    breadcrumbs.length - 1
                      ? 'active'
                      : ''
                  }
                  disabled={
                    index ===
                    breadcrumbs.length - 1
                  }
                  onClick={() => {
                    openBreadcrumb(
                      diagram,
                      index,
                    )
                  }}
                >
                  {diagram.nombre}
                </button>
              </span>
            ),
          )}
        </nav>

        <div className="diagram-zoom">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={zoom <= 0.6}
            onClick={() => {
              setZoom((value) =>
                Math.max(
                  0.6,
                  value - 0.1,
                ),
              )
            }}
          >
            <Minus size={16} />
          </button>

          <span>
            {Math.round(zoom * 100)}%
          </span>

          <button
            type="button"
            aria-label="Zoom in"
            disabled={zoom >= 1.5}
            onClick={() => {
              setZoom((value) =>
                Math.min(
                  1.5,
                  value + 0.1,
                ),
              )
            }}
          >
            <Plus size={16} />
          </button>
        </div>
      </div>

      {errorMessage && (
        <div className="diagram-error">
          {errorMessage}
        </div>
      )}

      <div className="diagram-meta">
        <span>
          {currentDiagram?.tipo ===
          'JOB'
            ? 'JOB'
            : 'TRANSFORMATION'}
        </span>
        <span>
          {currentDiagram?.total_elementos ??
            0}{' '}
          elements
        </span>
        <span>
          {currentDiagram?.total_hops ??
            0}{' '}
          hops
        </span>
        <span>
          Drag nodes to reorganize the
          diagram. Select a highlighted
          node to open its process.
        </span>
      </div>

      {currentDiagram?.tipo === 'TRANSFORMATION' &&
        isTechnicalPanelOpen && (
          <aside className="transformation-info-panel">
            <header className="transformation-info-header">
              <div>
                <span>TRANSFORMACIÓN</span>
                <h3>{currentDiagram.nombre}</h3>
                <small>{currentDiagram.ruta}</small>
              </div>

              <button
                type="button"
                onClick={() => setIsTechnicalPanelOpen(false)}
                aria-label="Cerrar información técnica"
              >
                <X size={18} />
              </button>
            </header>

            {currentDiagram.detalle_tecnico ? (
              <div className="transformation-info-content">
                <div className="transformation-summary-grid">
                  <span>
                    <strong>
                      {currentDiagram.detalle_tecnico.total_pasos}
                    </strong>
                    pasos
                  </span>
                  <span>
                    <strong>
                      {currentDiagram.detalle_tecnico.total_hops}
                    </strong>
                    conexiones
                  </span>
                  <span>
                    <strong>
                      {currentDiagram.detalle_tecnico.scripts_sql.length}
                    </strong>
                    scripts SQL
                  </span>
                </div>

                <section className="transformation-info-section">
                  <h4>
                    <Database size={16} />
                    Tablas consultadas
                  </h4>
                  {currentDiagram.detalle_tecnico.tablas_consultadas.length ? (
                    <div className="technical-chip-list">
                      {currentDiagram.detalle_tecnico.tablas_consultadas.map(
                        (table) => (
                          <span key={table.nombre_completo}>
                            {table.nombre_completo}
                          </span>
                        ),
                      )}
                    </div>
                  ) : (
                    <p>No se detectaron tablas consultadas.</p>
                  )}
                </section>

                <section className="transformation-info-section">
                  <h4>
                    <Database size={16} />
                    Tablas de destino
                  </h4>
                  {currentDiagram.detalle_tecnico.tablas_destino.length ? (
                    <div className="technical-chip-list destination">
                      {currentDiagram.detalle_tecnico.tablas_destino.map(
                        (table) => (
                          <span key={table.nombre_completo}>
                            {table.nombre_completo}
                          </span>
                        ),
                      )}
                    </div>
                  ) : (
                    <p>No se detectaron tablas escritas.</p>
                  )}
                </section>

                <section className="transformation-info-section">
                  <h4>
                    <Code2 size={16} />
                    SQL
                    <span
                      className={
                        currentDiagram.detalle_tecnico.tiene_sql
                          ? 'sql-status available'
                          : 'sql-status'
                      }
                    >
                      {currentDiagram.detalle_tecnico.tiene_sql
                        ? 'DISPONIBLE'
                        : 'NO DETECTADO'}
                    </span>
                  </h4>

                  {currentDiagram.detalle_tecnico.scripts_sql.map(
                    (script, index) => (
                      <details
                        className="sql-script-card"
                        key={`${script.paso}-${index}`}
                      >
                        <summary>
                          <span>
                            <strong>{script.paso}</strong>
                            <small>{script.tipo_paso}</small>
                          </span>
                          <ChevronRight size={16} />
                        </summary>
                        <pre>
                          <code>{script.sql}</code>
                        </pre>
                      </details>
                    ),
                  )}

                  {!currentDiagram.detalle_tecnico.tiene_sql && (
                    <p>
                      Esta transformación no contiene un script SQL
                      recuperable en el análisis guardado.
                    </p>
                  )}
                </section>

                <section className="transformation-info-section">
                  <h4>Tipos de pasos</h4>
                  <div className="step-type-list">
                    {currentDiagram.detalle_tecnico.tipos_paso.map((item) => (
                      <span key={item.tipo}>
                        <small>{item.tipo}</small>
                        <strong>{item.cantidad}</strong>
                      </span>
                    ))}
                  </div>
                </section>

                {(currentDiagram.detalle_tecnico.variables.length > 0 ||
                  currentDiagram.detalle_tecnico.parametros.length > 0) && (
                  <section className="transformation-info-section">
                    <h4>Variables y parámetros</h4>
                    <div className="technical-chip-list neutral">
                      {currentDiagram.detalle_tecnico.variables.map(
                        (variable) => (
                          <span key={`variable-${variable}`}>
                            {`\${${variable}}`}
                          </span>
                        ),
                      )}
                      {currentDiagram.detalle_tecnico.parametros.map(
                        (parameter) => (
                          <span key={`parameter-${parameter}`}>
                            #{parameter}
                          </span>
                        ),
                      )}
                    </div>
                  </section>
                )}

                {currentDiagram.detalle_tecnico.detalle_historico_limitado && (
                  <p className="technical-history-note">
                    Esta versión fue documentada antes de guardar scripts SQL.
                    Se muestra la información técnica disponible en la base de
                    datos.
                  </p>
                )}
              </div>
            ) : (
              <div className="transformation-info-empty">
                No hay información técnica disponible para esta transformación.
              </div>
            )}
          </aside>
        )}

      <div
        ref={scrollAreaRef}
        className={
          isPanning
            ? 'diagram-scroll-area panning'
            : 'diagram-scroll-area'
        }
        onPointerDown={startCanvasPan}
        onPointerMove={moveCanvasPan}
        onPointerUp={finishCanvasPan}
        onPointerCancel={finishCanvasPan}
      >
        {isLoading && (
          <div className="diagram-loading">
            <LoaderCircle size={28} />
            Loading diagram...
          </div>
        )}

        {!isLoading &&
          currentDiagram && (
            <div
              className="diagram-scaled-stage"
              style={{
                width:
                  canvasSize.width * zoom,
                height:
                  canvasSize.height * zoom,
              }}
            >
              <div
                className="diagram-canvas"
                style={{
                  width: canvasSize.width,
                  height: canvasSize.height,
                  transform: `scale(${zoom})`,
                }}
              >
                <svg
                  className="diagram-connections"
                  width={canvasSize.width}
                  height={canvasSize.height}
                  aria-hidden="true"
                >
                  <defs>
                    <marker
                      id="diagram-arrow-enabled"
                      markerWidth="10"
                      markerHeight="10"
                      refX="9"
                      refY="5"
                      orient="auto"
                      markerUnits="userSpaceOnUse"
                    >
                      <path
                        d="M0,0 L10,5 L0,10 Z"
                        fill="#8b82ff"
                      />
                    </marker>

                    <marker
                      id="diagram-arrow-disabled"
                      markerWidth="10"
                      markerHeight="10"
                      refX="9"
                      refY="5"
                      orient="auto"
                      markerUnits="userSpaceOnUse"
                    >
                      <path
                        d="M0,0 L10,5 L0,10 Z"
                        fill="#5d6274"
                      />
                    </marker>
                  </defs>

                  {currentDiagram.hops.map(
                    (hop, index) => {
                      const source =
                        positionByName.get(
                          hop.origen,
                        )

                      const target =
                        positionByName.get(
                          hop.destino,
                        )

                      if (!source || !target) {
                        return null
                      }

                      const connection =
                        calculateConnectionPoints(
                          source,
                          target,
                        )

                      if (!connection) {
                        return null
                      }

                      return (
                        <line
                          key={`${hop.origen}-${hop.destino}-${index}`}
                          x1={connection.x1}
                          y1={connection.y1}
                          x2={connection.x2}
                          y2={connection.y2}
                          className={
                            hop.habilitado
                              ? 'diagram-hop'
                              : 'diagram-hop disabled'
                          }
                          markerEnd={
                            hop.habilitado
                              ? 'url(#diagram-arrow-enabled)'
                              : 'url(#diagram-arrow-disabled)'
                          }
                        />
                      )
                    },
                  )}
                </svg>

                {positionedElements.map(
                  (element) => {
                    const isJob =
                      element.categoria ===
                      'ENTRY'

                    return (
                      <button
                        type="button"
                        key={element.id}
                        className={[
                          'diagram-node',
                          isJob
                            ? 'job-entry'
                            : 'transformation-step',
                          element.navegable
                            ? 'navigable'
                            : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        style={{
                          left:
                            element.displayX,
                          top:
                            element.displayY,
                          width: NODE_WIDTH,
                          height:
                            NODE_HEIGHT,
                        }}
                        aria-disabled={
                          !element.navegable
                        }
                        title={
                          element.navegable
                            ? `Open ${element.target?.nombre ?? element.nombre}`
                            : element.tipo
                        }
                        onPointerDown={(
                          event,
                        ) => {
                          startDragging(
                            event,
                            element,
                          )
                        }}
                        onPointerMove={
                          moveDraggedNode
                        }
                        onPointerUp={
                          finishDragging
                        }
                        onPointerCancel={
                          finishDragging
                        }
                        onClick={() => {
                          if (
                            suppressClickRef.current
                          ) {
                            suppressClickRef.current =
                              false
                            return
                          }

                          void openTarget(
                            element,
                          )
                        }}
                      >
                        <span className="diagram-node-icon">
                          {isJob ? (
                            <Workflow
                              size={18}
                            />
                          ) : (
                            <GitBranch
                              size={18}
                            />
                          )}
                        </span>

                        <span className="diagram-node-copy">
                          <strong>
                            {element.nombre}
                          </strong>

                          <small>
                            {element.tipo}
                          </small>
                        </span>

                        {element.navegable && (
                          <span className="diagram-open-indicator">
                            +
                          </span>
                        )}
                      </button>
                    )
                  },
                )}
              </div>
            </div>
          )}
      </div>
    </section>
  )
}

export default DiagramViewer
