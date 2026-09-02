import type { ComponentType } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Camera,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  ClipboardList,
  Clock3,
  Download,
  Ellipsis,
  ExternalLink,
  Filter,
  Gauge,
  Info,
  KeyRound,
  LogOut,
  Menu,
  MessageSquareText,
  Paperclip,
  PlugZap,
  RefreshCw,
  ScanLine,
  Search,
  Send,
  ServerCog,
  Settings,
  ShieldCheck,
  SunMoon,
  TriangleAlert,
  UserRound,
  Users,
  Warehouse,
  WifiOff,
  X,
  type LucideProps,
} from 'lucide-react'
import { RobotCheckGlyph, RobotGlyph } from './robotGlyphs'

export type IconName =
  | 'overview' | 'work' | 'robot' | 'robot-check' | 'reports' | 'analytics'
  | 'parks' | 'users' | 'roles' | 'integration' | 'safety' | 'system'
  | 'search' | 'scan' | 'more' | 'theme' | 'logout' | 'back' | 'forward'
  | 'close' | 'refresh' | 'warning' | 'critical' | 'success' | 'info'
  | 'offline' | 'attachment' | 'camera' | 'download' | 'send' | 'filter'
  | 'clock' | 'assignee' | 'menu' | 'chevron-down' | 'settings' | 'external-link'

const ICONS = {
  overview: Gauge,
  work: ClipboardList,
  robot: RobotGlyph,
  'robot-check': RobotCheckGlyph,
  reports: MessageSquareText,
  analytics: BarChart3,
  parks: Warehouse,
  users: Users,
  roles: KeyRound,
  integration: PlugZap,
  safety: ShieldCheck,
  system: ServerCog,
  search: Search,
  scan: ScanLine,
  more: Ellipsis,
  theme: SunMoon,
  logout: LogOut,
  back: ArrowLeft,
  forward: ArrowRight,
  close: X,
  refresh: RefreshCw,
  warning: TriangleAlert,
  critical: CircleAlert,
  success: CheckCircle2,
  info: Info,
  offline: WifiOff,
  attachment: Paperclip,
  camera: Camera,
  download: Download,
  send: Send,
  filter: Filter,
  clock: Clock3,
  assignee: UserRound,
  menu: Menu,
  'chevron-down': ChevronDown,
  settings: Settings,
  'external-link': ExternalLink,
} satisfies Record<IconName, ComponentType<LucideProps>>

export function Icon({ name, ...props }: { name: IconName } & LucideProps) {
  const Component = ICONS[name]
  return <Component {...props} aria-hidden="true" focusable="false" />
}
