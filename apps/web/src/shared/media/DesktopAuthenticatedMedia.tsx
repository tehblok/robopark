import { useEffect, useRef, useState, type AnchorHTMLAttributes, type ImgHTMLAttributes, type MouseEvent } from 'react'
import { desktopRuntime } from '../../pwa/desktopRuntime'

const MAX_IMAGE_BYTES = 16 * 1024 * 1024
const MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
const REQUEST_TIMEOUT_MS = 30_000
const DOWNLOAD_URL_LIFETIME_MS = 30_000

function isInternalApiPath(value: string): boolean {
  return value.startsWith('/api/') && !value.startsWith('//')
}

async function boundedBlob(response: Response, maxBytes: number, requiredTypePrefix?: string): Promise<Blob> {
  if (!response.ok) throw new Error(`authenticated_media_http_${response.status}`)
  const type = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase() ?? ''
  if (requiredTypePrefix && !type.startsWith(requiredTypePrefix)) throw new Error('authenticated_media_type_invalid')
  const announcedSize = Number(response.headers.get('content-length'))
  if (Number.isFinite(announcedSize) && announcedSize > maxBytes) throw new Error('authenticated_media_too_large')

  if (!response.body) {
    const blob = await response.blob()
    if (blob.size > maxBytes) throw new Error('authenticated_media_too_large')
    return blob
  }

  const reader = response.body.getReader()
  const chunks: Uint8Array<ArrayBuffer>[] = []
  let total = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > maxBytes) {
        await reader.cancel()
        throw new Error('authenticated_media_too_large')
      }
      chunks.push(new Uint8Array(value))
    }
  } finally {
    reader.releaseLock()
  }
  return new Blob(chunks, { type })
}

type ImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> & { src: string }

export function DesktopAuthenticatedImage({ src, ...props }: ImageProps) {
  const protectedSource = Boolean(desktopRuntime() && isInternalApiPath(src))
  const [resolved, setResolved] = useState<string | null>(() => protectedSource ? null : src)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>(() => protectedSource ? 'loading' : 'ready')

  useEffect(() => {
    if (!protectedSource) {
      setResolved(src)
      setState('ready')
      return
    }
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    let active = true
    let ownedUrl: string | null = null
    setResolved(null)
    setState('loading')
    void fetch(src, { cache: 'no-store', credentials: 'same-origin', signal: controller.signal })
      .then(response => boundedBlob(response, MAX_IMAGE_BYTES, 'image/'))
      .then(blob => {
        if (!active) return
        ownedUrl = URL.createObjectURL(blob)
        setResolved(ownedUrl)
        setState('ready')
      })
      .catch(error => {
        if (active && !(error instanceof DOMException && error.name === 'AbortError')) setState('error')
      })
      .finally(() => window.clearTimeout(timeout))
    return () => {
      active = false
      window.clearTimeout(timeout)
      controller.abort()
      if (ownedUrl) URL.revokeObjectURL(ownedUrl)
    }
  }, [protectedSource, src])

  return <img {...props} data-media-state={protectedSource ? state : undefined} src={resolved ?? undefined} />
}

type DownloadProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { href: string }

export function DesktopAuthenticatedDownload({ children, download, href, onClick, target, ...props }: DownloadProps) {
  const protectedSource = Boolean(desktopRuntime() && isInternalApiPath(href))
  const controller = useRef<AbortController | null>(null)
  const ownedUrls = useRef(new Set<string>())
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle')

  useEffect(() => () => {
    controller.current?.abort()
    for (const url of ownedUrls.current) URL.revokeObjectURL(url)
    ownedUrls.current.clear()
    controller.current = null
  }, [href])

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (!protectedSource || event.defaultPrevented) return
    event.preventDefault()
    if (controller.current) return
    const nextController = new AbortController()
    const timeout = window.setTimeout(() => nextController.abort(), REQUEST_TIMEOUT_MS)
    controller.current = nextController
    setState('loading')
    void fetch(href, { cache: 'no-store', credentials: 'same-origin', signal: nextController.signal })
      .then(response => boundedBlob(response, MAX_DOWNLOAD_BYTES))
      .then(blob => {
        if (nextController.signal.aborted || controller.current !== nextController) return
        const objectUrl = URL.createObjectURL(blob)
        ownedUrls.current.add(objectUrl)
        const temporary = document.createElement('a')
        temporary.href = objectUrl
        if (download === true) temporary.download = ''
        else if (typeof download === 'string') temporary.download = download
        temporary.style.display = 'none'
        document.body.append(temporary)
        temporary.click()
        temporary.remove()
        window.setTimeout(() => {
          if (!ownedUrls.current.delete(objectUrl)) return
          URL.revokeObjectURL(objectUrl)
        }, DOWNLOAD_URL_LIFETIME_MS)
        setState('idle')
      })
      .catch(error => {
        if (controller.current === nextController && !(error instanceof DOMException && error.name === 'AbortError')) setState('error')
      })
      .finally(() => {
        window.clearTimeout(timeout)
        if (controller.current === nextController) controller.current = null
      })
  }

  return <a
    {...props}
    aria-busy={protectedSource && state === 'loading' ? true : undefined}
    data-download-state={protectedSource ? state : undefined}
    download={protectedSource ? undefined : download}
    href={protectedSource ? '#' : href}
    onClick={handleClick}
    target={protectedSource ? undefined : target}
  >{children}</a>
}
