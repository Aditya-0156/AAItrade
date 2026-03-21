interface PnLBadgeProps {
  value: number | null | undefined
  showSign?: boolean
  className?: string
}

export function PnLBadge({ value, showSign = true, className = '' }: PnLBadgeProps) {
  if (value === null || value === undefined) {
    return <span className={`text-gray-500 ${className}`}>—</span>
  }

  const isPositive = value >= 0
  const color = isPositive ? 'text-profit' : 'text-loss'

  return (
    <span className={`${color} font-mono ${className}`}>
      {isPositive ? '+' : '-'}₹
      {Math.abs(value).toLocaleString('en-IN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}
    </span>
  )
}

export function PnLPercent({
  value,
  className = '',
}: {
  value: number | null | undefined
  className?: string
}) {
  if (value === null || value === undefined) {
    return <span className={`text-gray-500 ${className}`}>—</span>
  }

  const isPositive = value >= 0
  const color = isPositive ? 'text-profit' : 'text-loss'
  const sign = isPositive ? '+' : ''

  return (
    <span className={`${color} font-mono ${className}`}>
      {sign}{value.toFixed(2)}%
    </span>
  )
}
