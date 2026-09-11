import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react'
import './ResponsiveDisclosure.css'

const PHONE_MEDIA_QUERY = '(max-width: 599px)'

type DisclosureContextValue = {
  isPhone: boolean
  openId: string | undefined
  changeOpenId: (id: string | undefined) => void
  registerOpenChange: (id: string, callback: (open: boolean) => void) => () => void
}

const DisclosureContext = createContext<DisclosureContextValue | null>(null)

export type ResponsiveDisclosureGroupProps = {
  children: ReactNode
  controlledOpenId?: string | null
  initialOpenId?: string
  label: string
  onOpenIdChange?: (id: string | undefined) => void
}

function readIsPhone(): boolean {
  return typeof window !== 'undefined' && window.matchMedia(PHONE_MEDIA_QUERY).matches
}

export function ResponsiveDisclosureGroup({
  children,
  controlledOpenId,
  initialOpenId,
  label,
  onOpenIdChange,
}: ResponsiveDisclosureGroupProps): ReactElement {
  const [isPhone, setIsPhone] = useState(readIsPhone)
  const [internalOpenId, setInternalOpenId] = useState<string | undefined>(initialOpenId)
  const openId = controlledOpenId === undefined ? internalOpenId : controlledOpenId ?? undefined
  const callbacks = useRef(new Map<string, (open: boolean) => void>())
  const registerOpenChange = useCallback((id: string, callback: (open: boolean) => void) => {
    callbacks.current.set(id, callback)
    return () => { if (callbacks.current.get(id) === callback) callbacks.current.delete(id) }
  }, [])
  const changeOpenId = (nextId: string | undefined) => {
    if (nextId === openId) return
    if (openId) callbacks.current.get(openId)?.(false)
    if (nextId) callbacks.current.get(nextId)?.(true)
    if (controlledOpenId === undefined) setInternalOpenId(nextId)
    onOpenIdChange?.(nextId)
  }

  useEffect(() => {
    const media = window.matchMedia(PHONE_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setIsPhone(event.matches)
    setIsPhone(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  return (
    <DisclosureContext.Provider value={{ isPhone, openId, changeOpenId, registerOpenChange }}>
      <div aria-label={label} className="rp-responsive-disclosure-group" role="group">
        {children}
      </div>
    </DisclosureContext.Provider>
  )
}

export type ResponsiveDisclosureProps = {
  children: ReactNode
  id: string
  onOpenChange?: (open: boolean) => void
  summary?: ReactNode
  title: ReactNode
}

export function ResponsiveDisclosure({
  children,
  id,
  onOpenChange,
  summary,
  title,
}: ResponsiveDisclosureProps): ReactElement {
  const context = useContext(DisclosureContext)
  const generatedId = useId()
  const contentId = `rp-responsive-disclosure-${id}-${generatedId}`
  const isOpen = !context?.isPhone || context.openId === id
  useEffect(() => {
    if (!context || !onOpenChange) return
    return context.registerOpenChange(id, onOpenChange)
  }, [context, id, onOpenChange])

  return (
    <section className="rp-responsive-disclosure">
      <header className="rp-responsive-disclosure__header">
        <button
          aria-controls={contentId}
          aria-expanded={isOpen}
          className="rp-responsive-disclosure__trigger"
          disabled={context ? !context.isPhone : undefined}
          onClick={() => {
            if (context?.isPhone) {
              const nextOpen = context.openId !== id
              context.changeOpenId(nextOpen ? id : undefined)
            }
          }}
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
