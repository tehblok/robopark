import type { LucideProps } from 'lucide-react'

export function RobotGlyph({
  size = 24,
  color = 'currentColor',
  strokeWidth = 2,
  absoluteStrokeWidth: _absoluteStrokeWidth,
  ...props
}: LucideProps) {
  return (
    <svg
      {...props}
      color={color}
      data-rp-glyph="robot"
      fill="none"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
    >
      <path d="M10 7V4h4" />
      <rect height="9" rx="2" width="18" x="3" y="7" />
      <circle cx="7" cy="18" r="2" />
      <circle cx="17" cy="18" r="2" />
    </svg>
  )
}

export function RobotCheckGlyph({
  size = 24,
  color = 'currentColor',
  strokeWidth = 2,
  absoluteStrokeWidth: _absoluteStrokeWidth,
  ...props
}: LucideProps) {
  return (
    <svg
      {...props}
      color={color}
      data-rp-glyph="robot-check"
      fill="none"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
    >
      <path d="M10 7V4h4" />
      <rect height="9" rx="2" width="18" x="3" y="7" />
      <circle cx="7" cy="18" r="2" />
      <circle cx="17" cy="18" r="2" />
      <path d="m9 11 2 2 4-4" />
    </svg>
  )
}
