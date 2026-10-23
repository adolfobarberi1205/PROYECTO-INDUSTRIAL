import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type {
  PointerEvent as ReactPointerEvent,
} from 'react'

import {
  Braces,
  CircleDot,
  DatabaseZap,
  Eye,
  Hash,
  LoaderCircle,
  Maximize2,
  Minus,
  Minimize2,
  Plus,
  Rows3,
  X,
} from 'lucide-react'

import './VariableLineageDiagram.css'
import { API_BASE_URL } from '../config/api'

type LineageNode = {
  id: string
  tipo: string
  nombre: string
  proceso_id: string
  proceso: string
  componente: string
  estado: string
  nivel: number
  orden: number
  seleccionado: boolean
}

type LineageEdge = {
  id: string
  origen: string
  destino: string
  evento: string
  proceso: string
  componente: string
  estado: string
}

type VariableRoute = {
  variable: string
  etiqueta: string
  origen: string
  estado: string
  truncado: boolean
  procesos: string[]
  total_procesos: number
  nodos: LineageNode[]
  aristas: LineageEdge[]
  total_nodos: number
  total_aristas: number
}

type RouteResponse = {
  id_analisis: string
  ruta: VariableRoute
}

type VariableLineageDiagramProps = {
  analysisId?: string | null
  documentedProcessId?: number | null
  variableName: string
  onClose: () => void
}

type PositionedNode = LineageNode & {
  x: number
  y: number
}

type PanState = {
  pointerId: number
  startClientX: number
  startClientY: number
  startScrollLeft: number
  startScrollTop: number
}

const NODE_WIDTH = 214
const NODE_HEIGHT = 86
const COLUMN_GAP = 320
const ROW_GAP = 124
const CANVAS_PADDING = 80

async function readError(
  response: Response,
): Promise<string> {
  const data = await response
    .json()
    .catch(() => null)

  return data?.detail ??
    'The variable route could not be reconstructed.'
}

function nodeIcon(type: string) {
  switch (type) {
    case 'VARIABLE':
      return <Braces size={19} />
    case 'PARAMETRO':
      return <Hash size={19} />
    case 'CAMPO_FLUJO':
      return <Rows3 size={19} />
    case 'DINAMICO':
      return <DatabaseZap size={19} />
    case 'USO':
      return <Eye size={19} />
    default:
      return <CircleDot size={19} />
  }
}

function connectionPath(
  source: PositionedNode,
  target: PositionedNode,
) {
  const sourceCenterX =
    source.x + NODE_WIDTH / 2
  const sourceCenterY =
    source.y + NODE_HEIGHT / 2
  const targetCenterX =
    target.x + NODE_WIDTH / 2
  const targetCenterY =
    target.y + NODE_HEIGHT / 2

  const goesRight = targetCenterX >= sourceCenterX
  const startX = goesRight
    ? source.x + NODE_WIDTH + 5
    : source.x - 5
  const endX = goesRight
    ? target.x - 7
    : target.x + NODE_WIDTH + 7
  const distance = Math.max(
    70,
    Math.abs(endX - startX) * 0.42,
  )
  const control1X = goesRight
    ? startX + distance
    : startX - distance
  const control2X = goesRight
    ? endX - distance
    : endX + distance

  return [
    `M ${startX} ${sourceCenterY}`,
    `C ${control1X} ${sourceCenterY},`,
    `${control2X} ${targetCenterY},`,
    `${endX} ${targetCenterY}`,
  ].join(' ')
}

function VariableLineageDiagram({
  analysisId,
  documentedProcessId,
  variableName,
  onClose,
}: VariableLineageDiagramProps) {
  const viewerRef =
    useRef<HTMLElement | null>(null)
  const scrollRef =
    useRef<HTMLDivElement | null>(null)
  const panRef =
    useRef<PanState | null>(null)

  const [route, setRoute] =
    useState<VariableRoute | null>(null)
  const [isLoading, setIsLoading] =
    useState(true)
  const [errorMessage, setErrorMessage] =
    useState('')
  const [zoom, setZoom] =
    useState(1)
  const [isFullscreen, setIsFullscreen] =
    useState(false)
  const [isPanning, setIsPanning] =
    useState(false)

  useEffect(() => {
    let isActive = true

    async function loadRoute() {
      setIsLoading(true)
      setErrorMessage('')
      setRoute(null)
      setZoom(1)

      try {
        const parameters = new URLSearchParams({
          nombre: variableName,
        })
        const endpoint = documentedProcessId
          ? `${API_BASE_URL}/procesos-documentados/${documentedProcessId}/lineage/variable`
          : `${API_BASE_URL}/analisis/${analysisId}/lineage/variable`
        const response = await fetch(
          `${endpoint}?${parameters.toString()}`,
        )

        if (!response.ok) {
          throw new Error(await readError(response))
        }

        const data =
          (await response.json()) as RouteResponse

        if (isActive) {
          setRoute(data.ruta)
        }
      } catch (error) {
        if (isActive) {
          setErrorMessage(
            error instanceof Error
              ? error.message
              : 'The variable route could not be reconstructed.',
          )
        }
      } finally {
        if (isActive) {
          setIsLoading(false)
        }
      }
    }

    void loadRoute()

    return () => {
      isActive = false
    }
  }, [analysisId, documentedProcessId, variableName])

  useEffect(() => {
    function handleFullscreenChange() {
      setIsFullscreen(
        document.fullscreenElement === viewerRef.current,
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

  const positionedNodes = useMemo<PositionedNode[]>(
    () => {
      if (!route) {
        return []
      }

      const minLevel = Math.min(
        ...route.nodos.map((node) => node.nivel),
        0,
      )

      return route.nodos.map((node) => ({
        ...node,
        x:
          CANVAS_PADDING +
          (node.nivel - minLevel) * COLUMN_GAP,
        y:
          CANVAS_PADDING +
          node.orden * ROW_GAP,
      }))
    },
    [route],
  )

  const nodesById = useMemo(
    () => new Map(
      positionedNodes.map((node) => [node.id, node]),
    ),
    [positionedNodes],
  )

  const canvasSize = useMemo(() => ({
    width: Math.max(
      900,
      ...positionedNodes.map(
        (node) => node.x + NODE_WIDTH + CANVAS_PADDING,
      ),
    ),
    height: Math.max(
      540,
      ...positionedNodes.map(
        (node) => node.y + NODE_HEIGHT + CANVAS_PADDING,
      ),
    ),
  }), [positionedNodes])

  async function toggleFullscreen() {
    try {
      if (
        document.fullscreenElement === viewerRef.current
      ) {
        await document.exitFullscreen()
      } else {
        await viewerRef.current?.requestFullscreen()
      }
    } catch {
      setErrorMessage(
        'Fullscreen mode is not available in this browser.',
      )
    }
  }

  function startPan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (
      event.button !== 0 ||
      (event.target as HTMLElement).closest(
        '.variable-lineage-node',
      )
    ) {
      return
    }

    const scrollArea = scrollRef.current
    if (!scrollArea) {
      return
    }

    panRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScrollLeft: scrollArea.scrollLeft,
      startScrollTop: scrollArea.scrollTop,
    }
    scrollArea.setPointerCapture(event.pointerId)
    setIsPanning(true)
    event.preventDefault()
  }

  function movePan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const state = panRef.current
    const scrollArea = scrollRef.current

    if (
      !state ||
      !scrollArea ||
      state.pointerId !== event.pointerId
    ) {
      return
    }

    scrollArea.scrollLeft =
      state.startScrollLeft -
      (event.clientX - state.startClientX)
    scrollArea.scrollTop =
      state.startScrollTop -
      (event.clientY - state.startClientY)
  }

  function finishPan(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const state = panRef.current
    const scrollArea = scrollRef.current

    if (!state || state.pointerId !== event.pointerId) {
      return
    }

    panRef.current = null
    setIsPanning(false)

    if (scrollArea?.hasPointerCapture(event.pointerId)) {
      scrollArea.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <section
      ref={viewerRef}
      className="variable-lineage-viewer"
    >
      <header className="variable-lineage-header">
        <div>
          <span>LINAJE DE VARIABLE</span>
          <h2>
            {route?.etiqueta ?? `\${${variableName}}`}
          </h2>
          <p>
            Reconstructed origin, transformations and usage path
          </p>
        </div>

        <div className="variable-lineage-actions">
          <button
            type="button"
            onClick={() => void toggleFullscreen()}
            aria-label={
              isFullscreen
                ? 'Exit fullscreen'
                : 'Open lineage in fullscreen'
            }
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          >
            {isFullscreen ? (
              <Minimize2 size={19} />
            ) : (
              <Maximize2 size={19} />
            )}
          </button>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close lineage diagram"
            title="Close"
          >
            <X size={19} />
          </button>
        </div>
      </header>

      <div className="variable-lineage-toolbar">
        <div className="variable-lineage-meta">
          <span>{route?.origen ?? 'Reconstructing origin'}</span>
          <span>{route?.total_procesos ?? 0} processes</span>
          <span>{route?.total_nodos ?? 0} nodes</span>
          <span>{route?.total_aristas ?? 0} transitions</span>
        </div>

        <div className="variable-lineage-zoom">
          <button
            type="button"
            disabled={zoom <= 0.55}
            onClick={() =>
              setZoom((value) => Math.max(0.55, value - 0.1))
            }
            aria-label="Zoom out"
          >
            <Minus size={16} />
          </button>

          <span>{Math.round(zoom * 100)}%</span>

          <button
            type="button"
            disabled={zoom >= 1.5}
            onClick={() =>
              setZoom((value) => Math.min(1.5, value + 0.1))
            }
            aria-label="Zoom in"
          >
            <Plus size={16} />
          </button>
        </div>
      </div>

      {route?.truncado && (
        <div className="variable-lineage-warning">
          This route is very large. The first 180 connected
          elements are shown.
        </div>
      )}

      {errorMessage && (
        <div className="variable-lineage-error">
          {errorMessage}
        </div>
      )}

      <div
        ref={scrollRef}
        className={
          isPanning
            ? 'variable-lineage-scroll panning'
            : 'variable-lineage-scroll'
        }
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
      >
        {isLoading && (
          <div className="variable-lineage-loading">
            <LoaderCircle size={27} />
            Reconstructing variable route...
          </div>
        )}

        {!isLoading && route && (
          <div
            className="variable-lineage-stage"
            style={{
              width: canvasSize.width * zoom,
              height: canvasSize.height * zoom,
            }}
          >
            <div
              className="variable-lineage-canvas"
              style={{
                width: canvasSize.width,
                height: canvasSize.height,
                transform: `scale(${zoom})`,
              }}
            >
              <svg
                className="variable-lineage-connections"
                width={canvasSize.width}
                height={canvasSize.height}
                aria-hidden="true"
              >
                <defs>
                  <marker
                    id="variable-lineage-arrow"
                    markerWidth="10"
                    markerHeight="10"
                    refX="9"
                    refY="5"
                    orient="auto"
                    markerUnits="userSpaceOnUse"
                  >
                    <path
                      d="M0,0 L10,5 L0,10 Z"
                  fill="var(--theme-success-ink, #6fe2c1)"
                    />
                  </marker>
                  <marker
                    id="variable-lineage-arrow-unresolved"
                    markerWidth="10"
                    markerHeight="10"
                    refX="9"
                    refY="5"
                    orient="auto"
                    markerUnits="userSpaceOnUse"
                  >
                    <path
                      d="M0,0 L10,5 L0,10 Z"
                  fill="var(--theme-warning-ink, #d39b55)"
                    />
                  </marker>
                </defs>

                {route.aristas.map((edge) => {
                  const source = nodesById.get(edge.origen)
                  const target = nodesById.get(edge.destino)

                  if (!source || !target) {
                    return null
                  }

                  return (
                    <g key={edge.id}>
                      <path
                        d={connectionPath(source, target)}
                        className={
                          edge.estado === 'resuelto'
                            ? 'variable-lineage-edge'
                            : 'variable-lineage-edge unresolved'
                        }
                        markerEnd={
                          edge.estado === 'resuelto'
                            ? 'url(#variable-lineage-arrow)'
                            : 'url(#variable-lineage-arrow-unresolved)'
                        }
                      />
                    </g>
                  )
                })}
              </svg>

              {positionedNodes.map((node) => (
                <article
                  key={node.id}
                  className={[
                    'variable-lineage-node',
                    `type-${node.tipo.toLocaleLowerCase()}`,
                    node.seleccionado ? 'selected' : '',
                    node.estado !== 'resuelto' ? 'unresolved' : '',
                  ].filter(Boolean).join(' ')}
                  style={{
                    left: node.x,
                    top: node.y,
                    width: NODE_WIDTH,
                    height: NODE_HEIGHT,
                  }}
                  title={
                    [node.proceso, node.componente]
                      .filter(Boolean)
                      .join(' · ') || node.nombre
                  }
                >
                  <span className="variable-lineage-node-icon">
                    {nodeIcon(node.tipo)}
                  </span>

                  <span className="variable-lineage-node-copy">
                    <strong>{node.nombre}</strong>
                    <small>
                      {node.proceso || node.tipo.replace('_', ' ')}
                    </small>
                    {node.componente && (
                      <em>{node.componente}</em>
                    )}
                  </span>
                </article>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className="variable-lineage-legend">
        <span><i className="variable" /> Variable</span>
        <span><i className="field" /> Flow field</span>
        <span><i className="dynamic" /> Dynamic origin</span>
        <span><i className="parameter" /> Parameter</span>
        <span>Drag the empty background to move through the route.</span>
      </footer>
    </section>
  )
}

export default VariableLineageDiagram
