import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, type OperationsSlaPolicy, type User } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { FormField } from '../../design-system/forms/FormField'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { operationsAccessIdentity } from './operations'

type SlaPolicyApiClient = Pick<typeof api, 'operationsSlaPolicy' | 'updateOperationsSlaPolicy'>

export type SlaPolicyEditorProps = {
  parkId: number
  user: User
  apiClient?: SlaPolicyApiClient
  onSaved?: (policy: OperationsSlaPolicy) => void
}

function mayManagePolicy(user: User): boolean {
  return user.access_status === 'approved'
    && !user.must_change_password
    && (user.permissions ?? []).includes('parks.manage')
}

export function SlaPolicyEditor({ parkId, user, apiClient = api, onSaved }: SlaPolicyEditorProps) {
  const allowed = mayManagePolicy(user)
  const owner = `${operationsAccessIdentity(user)}:${parkId}:${allowed}`
  const generation = useRef(0)
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(allowed)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [error, setError] = useState<unknown>(null)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    if (!allowed) return
    const requested = ++generation.current
    setLoading(true)
    setLoadError(null)
    setError(null)
    setSuccess(false)
    void apiClient.operationsSlaPolicy(parkId).then((policy) => {
      if (generation.current !== requested) return
      setValue(policy.target_hours == null ? '' : String(policy.target_hours))
    }, (loadError) => {
      if (generation.current === requested) setLoadError(loadError)
    }).finally(() => {
      if (generation.current === requested) setLoading(false)
    })
    return () => { generation.current += 1 }
  }, [allowed, apiClient, owner, parkId])

  if (!allowed) return null
  if (loading) return <LoadingState label="Загружаем норматив SLA" />
  const validation = error instanceof Error && error.message === 'validation'
  if (loadError) {
    const failure = classifyApiError(loadError, 'Не удалось загрузить норматив SLA.')
    return <ErrorState title={failure.title} description={failure.description} />
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const target = value === '' ? null : Number(value)
    if (target !== null && (!/^\d+$/.test(value) || !Number.isInteger(target) || target < 1 || target > 8760)) {
      setError(new Error('validation'))
      setSuccess(false)
      return
    }
    const requested = generation.current
    setError(null)
    setSuccess(false)
    setSaving(true)
    void apiClient.updateOperationsSlaPolicy(parkId, { target_hours: target }).then((policy) => {
      if (generation.current !== requested) return
      setValue(policy.target_hours == null ? '' : String(policy.target_hours))
      setSuccess(true)
      onSaved?.(policy)
    }, (saveError) => {
      if (generation.current === requested) setError(saveError)
    }).finally(() => {
      if (generation.current === requested) setSaving(false)
    })
  }

  return <Panel title="Норматив SLA" description="Целевое число рабочих часов с 09:00 до 21:00 МСК. Пустое поле возвращает норматив 5 часов.">
    <form className="rp-sla-editor" onSubmit={submit}>
      <FormField id="operations-sla-target" label="Норматив, часов">
        <input id="operations-sla-target" inputMode="numeric" min="1" max="8760" step="1" value={value} onChange={(event) => { setValue(event.target.value); setError(null); setSuccess(false) }} />
      </FormField>
      <Button busy={saving} type="submit">Сохранить норматив</Button>
      {validation ? <p role="alert">Норматив — целое число от 1 до 8760.</p> : null}
      {error && !validation ? <p role="alert">{classifyApiError(error, 'Не удалось сохранить норматив SLA.').description}</p> : null}
      {success ? <p role="status">Норматив сохранён</p> : null}
    </form>
  </Panel>
}
