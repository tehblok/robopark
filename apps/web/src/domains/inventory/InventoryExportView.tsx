import { useEffect, useRef, useState } from 'react'
import { api, type InventoryExportParams, type Park, type User } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ErrorState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { classifyApiError } from '../../shared/api/classifyApiError'

type InventoryExportApi = Pick<typeof api, 'downloadInventoryExport'>

export function InventoryExportView({ selectedPark, parks, role, permissions, apiClient = api }: {
  selectedPark: Park
  parks: Park[]
  role?: User['role']
  permissions?: string[]
  apiClient?: InventoryExportApi
}) {
  const canExport = permissions?.includes('inventory.export') === true
  const canExportAll = role === 'admin' || role === 'royal'
  const [scope, setScope] = useState(String(selectedPark.id))
  const [format, setFormat] = useState<'csv' | 'xlsx'>('xlsx')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ReturnType<typeof classifyApiError> | null>(null)
  const [success, setSuccess] = useState('')
  const generation = useRef(0)

  useEffect(() => {
    generation.current += 1
    setScope(String(selectedPark.id))
    setBusy(false)
    setFailure(null)
    setSuccess('')
  }, [selectedPark.id])

  useEffect(() => () => { generation.current += 1 }, [])

  const resetFeedback = () => {
    generation.current += 1
    setBusy(false)
    setFailure(null)
    setSuccess('')
  }

  const download = async () => {
    const requestGeneration = ++generation.current
    const options: InventoryExportParams = scope === 'all'
      ? { scope: 'all', format }
      : { parkId: Number(scope), format }
    setBusy(true)
    setFailure(null)
    setSuccess('')
    try {
      await apiClient.downloadInventoryExport(options)
      if (requestGeneration === generation.current) setSuccess(`Файл ${format === 'xlsx' ? 'Excel' : 'CSV'} скачан.`)
    } catch (error) {
      if (requestGeneration === generation.current) setFailure(classifyApiError(error, 'Не удалось скачать выгрузку.'))
    } finally {
      if (requestGeneration === generation.current) setBusy(false)
    }
  }

  return <section className="inventory-catalog-view inventory-workflow-placeholder">
    <div><h2>Выгрузка парка {selectedPark.name}</h2><p>Скачивание идёт в вашу активную сессию.</p></div>
    {!canExport ? <ErrorState description="Обратитесь к администратору, чтобы получить доступ." title="Нет доступа к выгрузке" /> : <>
      <div className="inventory-create-grid">
        {canExportAll ? <FormField id="inventory-export-scope" label="Охват выгрузки"><select value={scope} onChange={event => { resetFeedback(); setScope(event.target.value) }}><option value="all">Все парки</option>{parks.map(park => <option key={park.id} value={park.id}>{park.name}{park.is_active === false ? ' (неактивен)' : ''}</option>)}</select></FormField> : null}
        <FormField id="inventory-export-format" label="Формат"><select value={format} onChange={event => { resetFeedback(); setFormat(event.target.value as 'csv' | 'xlsx') }}><option value="xlsx">Excel (.xlsx)</option><option value="csv">CSV</option></select></FormField>
      </div>
      <div className="inventory-global-actions"><Button busy={busy} leadingIcon="download" onClick={() => void download()} type="button">Скачать {format === 'xlsx' ? 'Excel' : 'CSV'}</Button></div>
      {success ? <p className="inventory-notice" role="status">{success}</p> : null}
      {failure ? <ErrorState description={failure.description} requestId={failure.requestId} title={failure.title} /> : null}
    </>}
  </section>
}
