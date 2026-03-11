import type { ServiceMetricTrendPoint } from '@/lib/adapters/service-metrics'

interface ServiceMetricTrendChartProps {
  points: ServiceMetricTrendPoint[]
  color: string
  fill: string
  formatValue: (value: number) => string
}

interface ChartGeometry {
  areaPath: string
  linePath: string
  width: number
  height: number
  latestY: number
  minValue: number
  maxValue: number
}

function buildChartGeometry(points: ServiceMetricTrendPoint[]): ChartGeometry | null {
  if (points.length === 0) {
    return null
  }

  const width = 420
  const height = 180
  const paddingX = 16
  const paddingY = 20
  const innerWidth = width - paddingX * 2
  const innerHeight = height - paddingY * 2
  const values = points.map((point) => point.value)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const range = maxValue - minValue
  const safeRange = range === 0 ? Math.max(1, Math.abs(maxValue) || 1) : range

  const coordinates = points.map((point, index) => {
    const x =
      paddingX + (points.length === 1 ? innerWidth / 2 : (index / (points.length - 1)) * innerWidth)
    const normalized = range === 0 ? 0.5 : (point.value - minValue) / safeRange
    const y = paddingY + innerHeight - normalized * innerHeight
    return { x, y }
  })

  const linePath = coordinates
    .map((coordinate, index) => `${index === 0 ? 'M' : 'L'} ${coordinate.x.toFixed(2)} ${coordinate.y.toFixed(2)}`)
    .join(' ')

  const areaPath = [
    linePath,
    `L ${coordinates.at(-1)?.x.toFixed(2)} ${(height - paddingY).toFixed(2)}`,
    `L ${coordinates[0]?.x.toFixed(2)} ${(height - paddingY).toFixed(2)}`,
    'Z',
  ].join(' ')

  return {
    areaPath,
    linePath,
    width,
    height,
    latestY: coordinates.at(-1)?.y ?? height / 2,
    minValue,
    maxValue,
  }
}

export function ServiceMetricTrendChart({
  points,
  color,
  fill,
  formatValue,
}: ServiceMetricTrendChartProps) {
  const geometry = buildChartGeometry(points)

  if (!geometry) {
    return (
      <div className="flex h-44 items-center justify-center rounded-md border border-dashed border-border/70 bg-muted/15 text-sm text-muted-foreground">
        No retained samples for this time range.
      </div>
    )
  }

  const gridRows = [0.15, 0.4, 0.65, 0.9]
  const latestPoint = points.at(-1)

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
        <div>
          <p className="uppercase tracking-wide">Max</p>
          <p className="mt-1 font-mono text-sm text-foreground">{formatValue(geometry.maxValue)}</p>
        </div>
        <div className="text-right">
          <p className="uppercase tracking-wide">Latest</p>
          <p className="mt-1 font-mono text-sm text-foreground">
            {latestPoint ? formatValue(latestPoint.value) : 'N/A'}
          </p>
        </div>
      </div>
      <div className="overflow-hidden rounded-md border border-border/70 bg-muted/10 p-3">
        <svg viewBox={`0 0 ${geometry.width} ${geometry.height}`} className="h-44 w-full" preserveAspectRatio="none">
          {gridRows.map((row, index) => {
            const y = geometry.height * row
            return (
              <line
                key={index}
                x1="0"
                y1={y}
                x2={geometry.width}
                y2={y}
                stroke="currentColor"
                strokeOpacity="0.08"
              />
            )
          })}
          <path d={geometry.areaPath} fill={fill} />
          <path d={geometry.linePath} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" />
          <line
            x1={geometry.width - 1}
            y1={geometry.latestY}
            x2={geometry.width}
            y2={geometry.latestY}
            stroke={color}
            strokeWidth="6"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <div className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
        <div>
          <p className="uppercase tracking-wide">Min</p>
          <p className="mt-1 font-mono text-sm text-foreground">{formatValue(geometry.minValue)}</p>
        </div>
        <div className="text-right">
          <p className="uppercase tracking-wide">Samples</p>
          <p className="mt-1 font-mono text-sm text-foreground">{points.length}</p>
        </div>
      </div>
    </div>
  )
}
