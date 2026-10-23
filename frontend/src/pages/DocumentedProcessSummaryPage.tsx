import {
  Boxes,
  Braces,
  Database,
  FileText,
  GitBranch,
  Hash,
  Network,
  Table2,
  Workflow,
} from 'lucide-react'

import './DocumentedProcessSummaryPage.css'

export type DocumentedProcess = {
  id: number
  nombre: string
  descripcion: string
  repositorio: {
    id: number
    nombre: string
    fecha_carga: string | null
  }
  job_principal: {
    id: number
    nombre: string
    carpeta: string
  }
  resumen: {
    componentes: number
    jobs: number
    transformaciones: number
    diagramas: number
    dimensiones: number
    hechos: number
    variables: number
    parametros: number
  }
}

type PageName =
  | 'documented-summary'
  | 'diagrams'
  | 'lineage'
  | 'documentation'

type Props = {
  process: DocumentedProcess
  onNavigate: (page: PageName) => void
}

function DocumentedProcessSummaryPage({
  process,
  onNavigate,
}: Props) {
  const summary = process.resumen

  return (
    <main className="documented-summary-page">
      <section className="documented-summary-hero">
        <div>
          <span className="documented-context-label">
            PROCESO ETL DOCUMENTADO
          </span>
          <h1>{process.nombre}</h1>
          <p>
            Root Job <strong>{process.job_principal.nombre}</strong>
            {' · '}{process.job_principal.carpeta}
          </p>
        </div>

        <div className="documented-summary-state">
          <Database size={21} />
          <div>
            <strong>Stored in PostgreSQL</strong>
            <span>No XML upload is required</span>
          </div>
        </div>
      </section>

      <section className="documented-summary-metrics">
        <article>
          <Boxes size={21} />
          <span>COMPONENTES</span>
          <strong>{summary.componentes}</strong>
          <small>{summary.jobs} Jobs · {summary.transformaciones} transformations</small>
        </article>
        <article>
          <Network size={21} />
          <span>DIAGRAMAS</span>
          <strong>{summary.diagramas}</strong>
          <small>Navigable process diagrams</small>
        </article>
        <article>
          <Table2 size={21} />
          <span>TABLAS DWH</span>
          <strong>{summary.dimensiones + summary.hechos}</strong>
          <small>{summary.dimensiones} DIM · {summary.hechos} FACT</small>
        </article>
        <article>
          <Braces size={21} />
          <span>VARIABLES</span>
          <strong>{summary.variables}</strong>
          <small>{summary.parametros} parameters</small>
        </article>
      </section>

      <section className="documented-summary-actions">
        <button type="button" onClick={() => onNavigate('diagrams')}>
          <Workflow size={24} />
          <span><strong>Explorar diagramas</strong><small>Navegar entre Jobs y transformaciones</small></span>
        </button>
        <button type="button" onClick={() => onNavigate('lineage')}>
          <GitBranch size={24} />
          <span><strong>Explorar linaje</strong><small>Variables, parámetros y tablas FACT y DIM</small></span>
        </button>
        <button type="button" onClick={() => onNavigate('documentation')}>
          <FileText size={24} />
          <span><strong>Abrir documentación</strong><small>Inventario completo y metadatos guardados</small></span>
        </button>
      </section>

      <section className="documented-summary-scope">
        <div><Workflow size={18} /><span>Jobs</span><strong>{summary.jobs}</strong></div>
        <div><Network size={18} /><span>Transformations</span><strong>{summary.transformaciones}</strong></div>
        <div><Database size={18} /><span>DIM tables</span><strong>{summary.dimensiones}</strong></div>
        <div><Table2 size={18} /><span>FACT tables</span><strong>{summary.hechos}</strong></div>
        <div><Hash size={18} /><span>Parameters</span><strong>{summary.parametros}</strong></div>
      </section>
    </main>
  )
}

export default DocumentedProcessSummaryPage
