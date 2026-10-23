import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  GitBranch,
  Search,
  Sparkles,
} from 'lucide-react'
import {
  type Dispatch,
  type SetStateAction,
  useEffect,
  useMemo,
  useState,
} from 'react'

import './TableFieldLineage.css'

export type FieldLineage = {
  nombre: string
  campo_flujo: string
  rol?: string
  tipo_origen: string
  detalle: string
  campos_origen: string[]
  tablas_origen: string[]
  fuentes_origen: string[]
  claves_dimension?: {
    campo_flujo: string
    campo_dimension: string
  }[]
}

export type TableLineage = {
  nombre: string
  tipo: 'FACT' | 'DIM'
  total_campos: number
  campos: FieldLineage[]
  dimensiones?: string[]
  tablas_fuente?: string[]
  fuentes_origen?: string[]
}

type TableFieldLineageProps = {
  tables: TableLineage[]
  kind: 'FACT' | 'DIM'
  dimensionTables?: TableLineage[]
}

type DimensionContinuation = {
  table: TableLineage
  sources: {
    name: string
    type: 'table' | 'source'
  }[]
}

function originLabel(originType: string) {
  const labels: Record<string, string> = {
    CLAVE_DIMENSION: 'Dimension lookup',
    ATRIBUTO_DIMENSION: 'Dimension attribute',
    CALCULO: 'Calculation',
    CONSULTA_SQL: 'SQL query',
    VARIABLE: 'Variable',
    ARCHIVO_EXCEL: 'Excel input',
    DATOS_INTERNOS: 'Internal data',
    SCRIPT: 'Script',
    INFORMACION_SISTEMA: 'System information',
    NO_DETERMINADO: 'Origin not determined',
  }

  return labels[originType] ?? originType.replaceAll('_', ' ')
}

function normalizedTableName(name: string) {
  const cleaned = name
    .replaceAll('"', '')
    .replaceAll('`', '')
    .replaceAll('[', '')
    .replaceAll(']', '')
    .trim()

  return (cleaned.split('.').filter(Boolean).pop() ?? cleaned)
    .toLocaleLowerCase()
}

function TableFieldLineage({
  tables,
  kind,
  dimensionTables = [],
}: TableFieldLineageProps) {
  const [selectedTableName, setSelectedTableName] = useState('')
  const [searchText, setSearchText] = useState('')
  const [expandedFields, setExpandedFields] = useState<Set<string>>(
    new Set(),
  )
  const [expandedOperations, setExpandedOperations] = useState<
    Set<string>
  >(new Set())

  useEffect(() => {
    setSelectedTableName(tables[0]?.nombre ?? '')
    setSearchText('')
    setExpandedFields(new Set())
    setExpandedOperations(new Set())
  }, [tables, kind])

  const filteredTables = useMemo(() => {
    const search = searchText.trim().toLocaleLowerCase()

    return tables.filter(
      (table) =>
        !search || table.nombre.toLocaleLowerCase().includes(search),
    )
  }, [tables, searchText])

  const selectedTable =
    tables.find((table) => table.nombre === selectedTableName) ??
    tables[0] ??
    null

  function toggleSet(
    setter: Dispatch<SetStateAction<Set<string>>>,
    key: string,
  ) {
    setter((current) => {
      const next = new Set(current)

      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }

      return next
    })
  }

  if (tables.length === 0) {
    return (
      <section className="table-lineage-empty">
        <GitBranch size={28} />
        <h2>No hay linaje disponible para {kind}</h2>
        <p>
          Reconstruct the process again to generate table and field
          lineage.
        </p>
      </section>
    )
  }

  return (
    <section className={`table-lineage-workspace ${kind.toLowerCase()}`}>
      <aside className="table-lineage-catalog">
        <div className="table-lineage-catalog-heading">
          <div>
            <span>TABLAS {kind}</span>
            <strong>{tables.length}</strong>
          </div>

          <label>
            <Search size={15} />
            <input
              type="search"
              placeholder={`Search ${kind}...`}
              value={searchText}
              onChange={(event) =>
                setSearchText(event.currentTarget.value)
              }
            />
          </label>
        </div>

        <div className="table-lineage-table-list">
          {filteredTables.map((table) => (
            <button
              type="button"
              className={
                selectedTable?.nombre === table.nombre
                  ? 'selected'
                  : ''
              }
              key={table.nombre}
              onClick={() => {
                setSelectedTableName(table.nombre)
                setExpandedFields(new Set())
                setExpandedOperations(new Set())
              }}
            >
              <Database size={17} />
              <span>{table.nombre}</span>
              <small>{table.total_campos} fields</small>
              <ChevronRight size={15} />
            </button>
          ))}
        </div>
      </aside>

      {selectedTable && (
        <main className="table-field-lineage-panel">
          <header className="table-field-lineage-heading">
            <div>
              <span>{kind} SELECCIONADA</span>
              <h2>{selectedTable.nombre}</h2>
              <p>
                Expand a field to trace it from the final table back to
                its source.
              </p>
            </div>
            <strong>{selectedTable.total_campos} fields</strong>
          </header>

          <div className="table-field-lineage-list">
            {selectedTable.campos.map((field) => {
              const fieldKey = `${selectedTable.nombre}:${field.nombre}`
              const isFieldExpanded = expandedFields.has(fieldKey)
              const isOperationExpanded = expandedOperations.has(fieldKey)
              const sources = [
                ...field.tablas_origen.map((name) => ({
                  name,
                  type: 'table' as const,
                })),
                ...field.fuentes_origen.map((name) => ({
                  name,
                  type: 'source' as const,
                })),
              ]
              const dimensionContinuations = sources.reduce<
                DimensionContinuation[]
              >((continuations, source) => {
                if (kind !== 'FACT' || source.type !== 'table') {
                  return continuations
                }

                const dimension = dimensionTables.find(
                  (candidate) =>
                    normalizedTableName(candidate.nombre) ===
                    normalizedTableName(source.name),
                )

                if (!dimension) {
                  return continuations
                }

                const originalSources = [
                  ...(dimension.tablas_fuente ?? []).map((name) => ({
                    name,
                    type: 'table' as const,
                  })),
                  ...(dimension.fuentes_origen ?? []).map((name) => ({
                    name,
                    type: 'source' as const,
                  })),
                  ...dimension.campos.flatMap((dimensionField) => [
                    ...dimensionField.tablas_origen.map((name) => ({
                      name,
                      type: 'table' as const,
                    })),
                    ...dimensionField.fuentes_origen.map((name) => ({
                      name,
                      type: 'source' as const,
                    })),
                  ]),
                ].filter(
                  (source, index, allSources) =>
                    allSources.findIndex(
                      (candidate) =>
                        candidate.type === source.type &&
                        normalizedTableName(candidate.name) ===
                          normalizedTableName(source.name),
                    ) === index,
                )

                if (originalSources.length > 0) {
                  continuations.push({
                    table: dimension,
                    sources: originalSources,
                  })
                }

                return continuations
              }, [])
              const dimensionSourceNames = new Set(
                dimensionContinuations.map((continuation) =>
                  normalizedTableName(continuation.table.nombre),
                ),
              )

              return (
                <article
                  className={
                    isFieldExpanded
                      ? 'table-field-lineage-item expanded'
                      : 'table-field-lineage-item'
                  }
                  key={fieldKey}
                >
                  <button
                    type="button"
                    className="table-field-toggle"
                    onClick={() => toggleSet(setExpandedFields, fieldKey)}
                  >
                    <span>
                      {isFieldExpanded ? (
                        <ChevronDown size={17} />
                      ) : (
                        <ChevronRight size={17} />
                      )}
                      <strong>{field.nombre}</strong>
                    </span>
                    <small>{originLabel(field.tipo_origen)}</small>
                  </button>

                  {isFieldExpanded && (
                    <>
                    <div className="field-origin-flow">
                      <div className="lineage-flow-node target">
                        <span>CAMPO FINAL</span>
                        <strong>{field.nombre}</strong>
                        {field.campo_flujo &&
                          field.campo_flujo !== field.nombre && (
                            <small>Stream: {field.campo_flujo}</small>
                          )}
                      </div>

                      <div className="lineage-flow-arrow">
                        <ArrowLeft size={25} />
                        <span>PROVIENE DE</span>
                      </div>

                      <button
                        type="button"
                        className={
                          isOperationExpanded
                            ? 'lineage-flow-node operation expanded'
                            : 'lineage-flow-node operation'
                        }
                        onClick={() =>
                          toggleSet(setExpandedOperations, fieldKey)
                        }
                      >
                        <span>TRANSFORMACIÓN</span>
                        <strong>{originLabel(field.tipo_origen)}</strong>
                        <small>
                          {isOperationExpanded
                            ? 'Hide details'
                            : 'Click for details'}
                        </small>

                        {isOperationExpanded && (
                          <div className="operation-specifications">
                            <p>{field.detalle}</p>
                            {field.campos_origen.length > 0 && (
                              <div>
                                {field.campos_origen.map((sourceField) => (
                                  <span key={sourceField}>{sourceField}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </button>

                      <div className="lineage-flow-arrow">
                        <ArrowLeft size={25} />
                        <span>RECIBE DE</span>
                      </div>

                      <div className="lineage-source-nodes">
                        {sources.length > 0 ? (
                          sources.map((source) => (
                            <div
                              className={
                                dimensionSourceNames.has(
                                  normalizedTableName(source.name),
                                )
                                  ? 'lineage-flow-node source dimension-source'
                                  : 'lineage-flow-node source'
                              }
                              key={`${source.type}:${source.name}`}
                            >
                              {source.type === 'table' ? (
                                <Database size={16} />
                              ) : (
                                <FileText size={16} />
                              )}
                              <span>
                                {dimensionSourceNames.has(
                                  normalizedTableName(source.name),
                                )
                                  ? 'SOURCE DIMENSION'
                                  : source.type === 'table'
                                    ? 'SOURCE TABLE'
                                  : 'SOURCE'}
                              </span>
                              <strong>{source.name}</strong>
                              {dimensionSourceNames.has(
                                normalizedTableName(source.name),
                              ) && <small>Lineage continues below</small>}
                            </div>
                          ))
                        ) : (
                          <div className="lineage-flow-node source generated">
                            <Sparkles size={16} />
                            <span>VALOR GENERADO</span>
                            <strong>{originLabel(field.tipo_origen)}</strong>
                          </div>
                        )}
                      </div>
                    </div>

                    {dimensionContinuations.map((continuation) => (
                      <section
                        className="dimension-lineage-continuation"
                        key={`${fieldKey}:${continuation.table.nombre}`}
                      >
                        <header>
                          <span>ORIGEN DE LA DIMENSIÓN</span>
                          <strong>{continuation.table.nombre}</strong>
                          <small>
                            Original data sources used to construct this
                            dimension.
                          </small>
                        </header>

                        <div className="dimension-original-source-flow">
                          <div className="dimension-source-anchor">
                            <Database size={17} />
                            <span>DIMENSIÓN</span>
                            <strong>{continuation.table.nombre}</strong>
                          </div>

                          <div className="lineage-flow-arrow">
                            <ArrowLeft size={25} />
                            <span>PROVIENE DE</span>
                          </div>

                          <div className="dimension-original-source-list">
                            {continuation.sources.map((source) => (
                              <div
                                className="lineage-flow-node source"
                                key={`${fieldKey}:${continuation.table.nombre}:${source.type}:${source.name}`}
                              >
                                {source.type === 'table' ? (
                                  <Database size={16} />
                                ) : (
                                  <FileText size={16} />
                                )}
                                <span>
                                  {source.type === 'table'
                                    ? 'ORIGINAL SOURCE TABLE'
                                    : 'ORIGINAL SOURCE'}
                                </span>
                                <strong>{source.name}</strong>
                              </div>
                            ))}
                          </div>
                        </div>
                      </section>
                    ))}
                    </>
                  )}
                </article>
              )
            })}
          </div>
        </main>
      )}
    </section>
  )
}

export default TableFieldLineage
