import {
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  Braces,
  ChevronRight,
  Database,
  GitBranch,
  Hash,
  Search,
  ShieldCheck,
  Tag,
  Table2,
} from 'lucide-react'

import './LineagePage.css'
import { API_BASE_URL } from '../config/api'
import VariableLineageDiagram from './VariableLineageDiagram'
import TableFieldLineage, {
  type TableLineage,
} from './TableFieldLineage'

type PageName =
  | 'overview'
  | 'processes'
  | 'documented-summary'
  | 'diagrams'
  | 'lineage'
  | 'documentation'

type LineagePageProps = {
  analysisId: string | null
  documentedProcessId?: number | null
  onNavigate: (page: PageName) => void
}

type VariableItem = {
  nombre: string
  etiqueta: string
  valor: string
  alcance: string
  procesos: string[]
  usado_en: number
  evidencias: string[]
  total_evidencias: number
  componentes: string[]
  total_componentes: number
  parametros_alimentados: string[]
}

type ParameterItem = {
  nombre: string
  etiqueta: string
  declarado_en: string[]
  total_declaraciones: number
  referenciado_en: string[]
  total_referencias: number
  aparece_en_llamadas: string[]
  total_llamadas: number
  procesos: string[]
  usado_en: number
}

type LineageResponse = {
  id_analisis: string
  analisis: {
    job_principal: string
    carpeta: string
  }
  totales: {
    variables: number
    parametros: number
    tablas_fact: number
    tablas_dim: number
  }
  variables: VariableItem[]
  parametros: ParameterItem[]
  tablas: {
    facts: TableLineage[]
    dimensions: TableLineage[]
  }
  politica_valores: string
}

type SelectedCategory =
  | 'variables'
  | 'parameters'
  | 'facts'
  | 'dimensions'
  | null

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

  return 'The lineage information could not be loaded.'
}

function LineagePage({
  analysisId,
  documentedProcessId,
  onNavigate,
}: LineagePageProps) {
  const [lineage, setLineage] =
    useState<LineageResponse | null>(null)

  const [category, setCategory] =
    useState<SelectedCategory>(null)

  const [searchText, setSearchText] =
    useState('')

  const [selectedVariable, setSelectedVariable] =
    useState<VariableItem | null>(null)

  const [selectedParameter, setSelectedParameter] =
    useState<ParameterItem | null>(null)

  const [isLoading, setIsLoading] =
    useState(false)

  const [errorMessage, setErrorMessage] =
    useState('')

  useEffect(() => {
    setLineage(null)
    setCategory(null)
    setSearchText('')
    setSelectedVariable(null)
    setSelectedParameter(null)

    if (!analysisId && !documentedProcessId) {
      return
    }

    async function loadLineage() {
      setIsLoading(true)
      setErrorMessage('')

      try {
        const endpoint = documentedProcessId
          ? `${API_BASE_URL}/procesos-documentados/${documentedProcessId}/lineage`
          : `${API_BASE_URL}/analisis/${analysisId}/lineage`
        const response = await fetch(endpoint)

        if (!response.ok) {
          throw new Error(
            await readResponseError(response),
          )
        }

        const data =
          (await response.json()) as LineageResponse

        setLineage({
          ...data,
          totales: {
            ...data.totales,
            tablas_fact: data.totales.tablas_fact ?? 0,
            tablas_dim: data.totales.tablas_dim ?? 0,
          },
          tablas: data.tablas ?? {
            facts: [],
            dimensions: [],
          },
        })
      } catch (error) {
        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'The lineage information could not be loaded.',
        )
      } finally {
        setIsLoading(false)
      }
    }

    void loadLineage()
  }, [analysisId, documentedProcessId])

  const hasLineageContext = Boolean(
    analysisId || documentedProcessId,
  )

  const filteredVariables = useMemo(() => {
    const search = searchText
      .trim()
      .toLocaleLowerCase()

    if (!lineage) {
      return []
    }

    return lineage.variables.filter((variable) =>
      !search ||
      variable.nombre
        .toLocaleLowerCase()
        .includes(search) ||
      variable.procesos.some((process) =>
        process.toLocaleLowerCase().includes(search),
      ),
    )
  }, [lineage, searchText])

  const filteredParameters = useMemo(() => {
    const search = searchText
      .trim()
      .toLocaleLowerCase()

    if (!lineage) {
      return []
    }

    return lineage.parametros.filter((parameter) =>
      !search ||
      parameter.nombre
        .toLocaleLowerCase()
        .includes(search) ||
      parameter.procesos.some((process) =>
        process.toLocaleLowerCase().includes(search),
      ),
    )
  }, [lineage, searchText])

  function selectCategory(
    nextCategory: Exclude<SelectedCategory, null>,
  ) {
    setCategory(nextCategory)
    setSearchText('')
    setSelectedVariable(null)
    setSelectedParameter(null)
  }

  return (
    <div className="lineage-workspace">
      <main className="lineage-content">
        {!hasLineageContext && (
          <section className="lineage-empty-analysis">
            <GitBranch size={32} />

            <h1>Reconstruct a process first</h1>

            <p>
              Lineage becomes available after selecting a
              root Job and completing its reconstruction.
            </p>

            <button
              type="button"
              onClick={() => onNavigate('processes')}
            >
              Go to Processes
              <ChevronRight size={17} />
            </button>
          </section>
        )}

        {hasLineageContext && isLoading && (
          <section className="lineage-loading">
            Loading lineage information...
          </section>
        )}

        {hasLineageContext && errorMessage && (
          <section className="lineage-error">
            {errorMessage}
          </section>
        )}

        {lineage && !isLoading && (
          <>
            <section className="lineage-category-grid">
              <button
                type="button"
                className={
                  category === 'variables'
                    ? 'lineage-category-card variables active'
                    : 'lineage-category-card variables'
                }
                onClick={() => selectCategory('variables')}
              >
                <span className="lineage-category-icon">
                  <Braces size={25} />
                </span>

                {category === 'variables' && (
                  <span className="lineage-active-badge">
                    ACTIVE
                  </span>
                )}

                <span className="lineage-category-label">
                  VARIABLES
                </span>

                <strong>
                  {lineage.totales.variables}
                </strong>

                <small>
                  ETL system variables
                </small>
              </button>

              <button
                type="button"
                className={
                  category === 'parameters'
                    ? 'lineage-category-card parameters active'
                    : 'lineage-category-card parameters'
                }
                onClick={() => selectCategory('parameters')}
              >
                <span className="lineage-category-icon">
                  <Hash size={27} />
                </span>

                {category === 'parameters' && (
                  <span className="lineage-active-badge">
                    ACTIVE
                  </span>
                )}

                <span className="lineage-category-label">
                  PARAMETERS
                </span>

                <strong>
                  {lineage.totales.parametros}
                </strong>

                <small>
                  Job & transformation parameters
                </small>
              </button>

              <button
                type="button"
                className={
                  category === 'facts'
                    ? 'lineage-category-card facts active'
                    : 'lineage-category-card facts'
                }
                onClick={() => selectCategory('facts')}
              >
                <span className="lineage-category-icon">
                  <Database size={26} />
                </span>

                {category === 'facts' && (
                  <span className="lineage-active-badge">ACTIVE</span>
                )}

                <span className="lineage-category-label">FACT TABLES</span>
                <strong>{lineage.totales.tablas_fact}</strong>
                <small>Field lineage from target to source</small>
              </button>

              <button
                type="button"
                className={
                  category === 'dimensions'
                    ? 'lineage-category-card dimensions active'
                    : 'lineage-category-card dimensions'
                }
                onClick={() => selectCategory('dimensions')}
              >
                <span className="lineage-category-icon">
                  <Table2 size={26} />
                </span>

                {category === 'dimensions' && (
                  <span className="lineage-active-badge">ACTIVE</span>
                )}

                <span className="lineage-category-label">
                  DIM TABLES
                </span>
                <strong>{lineage.totales.tablas_dim}</strong>
                <small>Source and transformation lineage</small>
              </button>
            </section>

            {!category && (
              <div className="lineage-category-prompt">
                Select a category above to explore its items
              </div>
            )}

            {category === 'variables' && (
              <section className="lineage-table-card">
                <div className="lineage-table-heading">
                  <div className="lineage-table-title">
                    <span className="lineage-section-icon variables">
                      <Braces size={22} />
                    </span>

                    <div>
                      <h2>System Variables</h2>
                      <p>Click a row to inspect its usage</p>
                    </div>
                  </div>

                  <label className="lineage-search">
                    <Search size={17} />
                    <input
                      type="search"
                      placeholder="Search variables..."
                      value={searchText}
                      onChange={(event) =>
                        setSearchText(event.currentTarget.value)
                      }
                    />
                  </label>
                </div>

                <div className="lineage-column-headings variables">
                  <span>Variable Name</span>
                  <span>Value</span>
                  <span>Scope</span>
                  <span>Used in</span>
                </div>

                <div className="lineage-rows">
                  {filteredVariables.map((variable) => (
                    <button
                      type="button"
                      className={
                        selectedVariable?.nombre === variable.nombre
                          ? 'lineage-row variables selected'
                          : 'lineage-row variables'
                      }
                      key={variable.nombre}
                      onClick={() => {
                        setSelectedVariable(variable)
                        setSelectedParameter(null)
                      }}
                    >
                      <span className="lineage-name-cell">
                        <Tag size={16} />
                        <strong>{variable.etiqueta}</strong>
                      </span>

                      <span className="lineage-protected-value">
                        <ShieldCheck size={15} />
                        {variable.valor}
                      </span>

                      <span>
                        <span className="lineage-scope-pill">
                          {variable.alcance}
                        </span>
                      </span>

                      <span className="lineage-usage-count">
                        <strong>{variable.usado_en}</strong>
                        {' '}processes
                      </span>
                    </button>
                  ))}
                </div>

                {filteredVariables.length === 0 && (
                  <div className="lineage-no-results">
                    No variables match your search.
                  </div>
                )}

                {selectedVariable && (
                  <VariableLineageDiagram
                    analysisId={analysisId}
                    documentedProcessId={documentedProcessId}
                    variableName={selectedVariable.nombre}
                    onClose={() => setSelectedVariable(null)}
                  />
                )}
              </section>
            )}

            {category === 'parameters' && (
              <section className="lineage-table-card">
                <div className="lineage-table-heading">
                  <div className="lineage-table-title">
                    <span className="lineage-section-icon parameters">
                      <Hash size={23} />
                    </span>

                    <div>
                      <h2>Process Parameters</h2>
                      <p>Click a row to inspect its usage</p>
                    </div>
                  </div>

                  <label className="lineage-search">
                    <Search size={17} />
                    <input
                      type="search"
                      placeholder="Search parameters..."
                      value={searchText}
                      onChange={(event) =>
                        setSearchText(event.currentTarget.value)
                      }
                    />
                  </label>
                </div>

                <div className="lineage-column-headings parameters">
                  <span>Parameter Name</span>
                  <span>Declared in</span>
                  <span>Referenced in</span>
                  <span>Calls</span>
                </div>

                <div className="lineage-rows">
                  {filteredParameters.map((parameter) => (
                    <button
                      type="button"
                      className={
                        selectedParameter?.nombre === parameter.nombre
                          ? 'lineage-row parameters selected'
                          : 'lineage-row parameters'
                      }
                      key={parameter.nombre}
                      onClick={() => {
                        setSelectedParameter(parameter)
                        setSelectedVariable(null)
                      }}
                    >
                      <span className="lineage-name-cell parameter">
                        <Hash size={16} />
                        <strong>{parameter.etiqueta}</strong>
                      </span>

                      <span className="lineage-number-cell">
                        <strong>{parameter.total_declaraciones}</strong>
                        {' '}processes
                      </span>

                      <span className="lineage-number-cell">
                        <strong>{parameter.total_referencias}</strong>
                        {' '}processes
                      </span>

                      <span className="lineage-usage-count parameter">
                        <strong>{parameter.total_llamadas}</strong>
                        {' '}calls
                      </span>
                    </button>
                  ))}
                </div>

                {filteredParameters.length === 0 && (
                  <div className="lineage-no-results">
                    No parameters match your search.
                  </div>
                )}

                {selectedParameter && (
                  <div className="lineage-detail-panel parameter">
                    <div>
                      <span>SELECTED PARAMETER</span>
                      <strong>{selectedParameter.etiqueta}</strong>
                    </div>

                    <div className="lineage-detail-metrics">
                      <span>
                        <strong>{selectedParameter.total_declaraciones}</strong>
                        declarations
                      </span>
                      <span>
                        <strong>{selectedParameter.total_referencias}</strong>
                        references
                      </span>
                      <span>
                        <strong>{selectedParameter.total_llamadas}</strong>
                        process calls
                      </span>
                    </div>

                    <div className="lineage-process-list parameter">
                      {selectedParameter.procesos.map((process) => (
                        <span key={process}>{process}</span>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            )}

            {category === 'facts' && (
              <TableFieldLineage
                tables={lineage.tablas.facts}
                kind="FACT"
                dimensionTables={lineage.tablas.dimensions}
              />
            )}

            {category === 'dimensions' && (
              <TableFieldLineage
                tables={lineage.tablas.dimensions}
                kind="DIM"
              />
            )}

            <footer className="lineage-data-policy">
              <ShieldCheck size={15} />
              {lineage.politica_valores}
            </footer>
          </>
        )}
      </main>
    </div>
  )
}

export default LineagePage
