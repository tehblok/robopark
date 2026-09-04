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
      <path d="M9 5h6" />
      <rect height="11" rx="4" width="18" x="3" y="7" />
      <path d="M7 18v1.5M17 18v1.5" />
      <path d="M8 14h8" />
      <circle cx="8" cy="11" r="1" />
      <circle cx="16" cy="11" r="1" />
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
      <rect height="10" rx="3.5" width="15" x="3" y="9" />
      <path d="M7 19v1M14 19v1" />
      <circle cx="7" cy="13" r="1" />
      <path d="m11 15 1.5 1.5 3-3.5" />
      <path d="M17 7a1 1 0 0 1 1-1" />
      <path d="M17 5a3 3 0 0 1 3 3" />
      <path d="M17 3a5 5 0 0 1 5 5" />
    </svg>
  )
}
