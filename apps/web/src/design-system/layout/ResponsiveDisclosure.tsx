import {
  createContext,
  useContext,
  useEffect,
  useId,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react'
import './ResponsiveDisclosure.css'

const PHONE_MEDIA_QUERY = '(max-width: 599px)'

type DisclosureContextValue = {
  isPhone: boolean
  openId: string | undefined
  setOpenId: (id: string | undefined) => void
}

const DisclosureContext = createContext<DisclosureContextValue | null>(null)

export type ResponsiveDisclosureGroupProps = {
  children: ReactNode
  initialOpenId?: string
  label: string
}

function readIsPhone(): boolean {
  return typeof window !== 'undefined' && window.matchMedia(PHONE_MEDIA_QUERY).matches
}

export function ResponsiveDisclosureGroup({
  children,
  initialOpenId,
  label,
}: ResponsiveDisclosureGroupProps): ReactElement {
  const [isPhone, setIsPhone] = useState(readIsPhone)
  const [openId, setOpenId] = useState<string | undefined>(initialOpenId)

  useEffect(() => {
    const media = window.matchMedia(PHONE_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setIsPhone(event.matches)
    setIsPhone(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  return (
    <DisclosureContext.Provider value={{ isPhone, openId, setOpenId }}>
      <div aria-label={label} className="rp-responsive-disclosure-group" role="group">
        {children}
      </div>
    </DisclosureContext.Provider>
  )
}

export type ResponsiveDisclosureProps = {
  children: ReactNode
  id: string
  summary?: ReactNode
  title: ReactNode
}

export function ResponsiveDisclosure({
  children,
  id,
  summary,
  title,
}: ResponsiveDisclosureProps): ReactElement {
  const context = useContext(DisclosureContext)
  const generatedId = useId()
  const contentId = `rp-responsive-disclosure-${id}-${generatedId}`
  const isOpen = !context?.isPhone || context.openId === id

  return (
    <section className="rp-responsive-disclosure">
      <header className="rp-responsive-disclosure__header">
        <button
          aria-controls={contentId}
          aria-expanded={isOpen}
          className="rp-responsive-disclosure__trigger"
          onClick={() => context?.setOpenId(context.openId === id ? undefined : id)}
          type="button"
        >
          {title}
        </button>
        {summary ? <div className="rp-responsive-disclosure__summary">{summary}</div> : null}
      </header>
      {isOpen ? (
        <div className="rp-responsive-disclosure__content" id={contentId}>
          {children}
        </div>
      ) : null}
    </section>
  )
}
