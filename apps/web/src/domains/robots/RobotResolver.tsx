import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ErrorState } from '../../design-system/feedback/AsyncState'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { parseRobotReference } from './robotReference'
import { rememberRobot } from './recentRobots'
import { RobotScanner, scannerSupported } from './RobotScanner'

export type RobotResolverApiClient = Pick<typeof api, 'emergencyResolve'>

export type RobotResolverProps = {
  userId: number
  value: string
  apiClient?: RobotResolverApiClient
  onValueChange: (value: string) => void
  onResolved: (result: Awaited<ReturnType<RobotResolverApiClient['emergencyResolve']>>) => void
  mode?: 'search' | 'inline'
}

export function RobotResolver({
  userId,
  value,
  apiClient = api,
  onValueChange,
  onResolved,
  mode = 'search',
}: RobotResolverProps) {
  const generationRef = useRef(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<DomainError | null>(null)
  const [scanOpen, setScanOpen] = useState(false)

  useEffect(() => {
    generationRef.current += 1
    return () => {
      generationRef.current += 1
    }
  }, [userId])

  const resolveInput = useCallback((raw: string) => {
    const reference = parseRobotReference(raw)
    if (!reference) {
      setError({
        kind: 'unknown',
        title: 'Проверьте номер робота',
        description: 'Введите номер, VIN или ссылку на робота',
        retryable: false,
      })
      return
    }

    const generation = ++generationRef.current
    setLoading(true)
    setError(null)
    void apiClient.emergencyResolve(reference).then((result) => {
      if (generation !== generationRef.current) return
      rememberRobot(userId, { query: reference, vin: result.vin })
      onResolved(result)
    }).catch((reason: unknown) => {
      if (generation !== generationRef.current) return
      setError(classifyApiError(reason, 'Не удалось найти робота.'))
    }).finally(() => {
      if (generation === generationRef.current) setLoading(false)
    })
  }, [apiClient, onResolved, userId])

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    resolveInput(value)
  }

  const safeBack = () => {
    setError(null)
    onValueChange('')
  }

  return (
    <section className={`rp-robot-resolver rp-robot-resolver--${mode}`}>
      <form className="rp-robot-resolver__form" onSubmit={submit}>
        <label htmlFor="robot-reference">Номер или VIN робота</label>
        <div className="rp-robot-resolver__controls">
          <input
            autoComplete="off"
            id="robot-reference"
            onChange={(event) => onValueChange(event.target.value)}
            placeholder="447 или YASADR00000000447"
            value={value}
          />
          <Button busy={loading} disabled={loading} leadingIcon="search" type="submit">
            Найти робота
          </Button>
          {scannerSupported() ? (
            <Button
              className="rp-robot-scan-action"
              leadingIcon="scan"
              onClick={() => setScanOpen(true)}
              type="button"
              variant="secondary"
            >
              Сканировать
            </Button>
          ) : null}
        </div>
      </form>

      {error ? (
        <div className="rp-robot-resolver__error">
          <ErrorState
            description={error.description}
            onRetry={error.retryable ? () => resolveInput(value) : undefined}
            requestId={error.requestId}
            title={error.title}
          />
          {error.kind === 'forbidden' || error.kind === 'not-found' ? (
            <Button onClick={safeBack} type="button" variant="secondary">Назад к поиску</Button>
          ) : null}
        </div>
      ) : null}

      <RobotScanner
        onCancel={() => setScanOpen(false)}
        onDetected={(detected) => {
          setScanOpen(false)
          onValueChange(detected)
          resolveInput(detected)
        }}
        open={scanOpen}
      />
    </section>
  )
}
