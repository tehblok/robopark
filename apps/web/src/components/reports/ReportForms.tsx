import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type ReportAttachmentKind, type ReportKindManual } from '../../api'
import { reportDraftKey, type ReportsApiClient } from '../../domains/reports/reports'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Alert } from '../PageShell'
import { Spinner } from '../ui/Feedback'

type ReportFormsProps = {
  apiClient?: ReportsApiClient
  ownerKey: string
  parkId: number
  principalId: number
  restoreDraft?: boolean
  onCreated: () => void
}

type FormKind = 'question' | 'problem'

type ReportDraft = {
  activeForm: FormKind
  trackerKey: string
  title: string
  body: string
}

const EMPTY_DRAFT: ReportDraft = {
  activeForm: 'question',
  trackerKey: '',
  title: '',
  body: '',
}

function loadDraft(principalId: number, parkId: number): ReportDraft {
  try {
    const stored = localStorage.getItem(reportDraftKey(principalId, parkId))
    if (!stored) return EMPTY_DRAFT
    const parsed = JSON.parse(stored) as Partial<ReportDraft>
    return {
      activeForm: parsed.activeForm === 'problem' ? 'problem' : 'question',
      trackerKey: typeof parsed.trackerKey === 'string' ? parsed.trackerKey : '',
      title: typeof parsed.title === 'string' ? parsed.title : '',
      body: typeof parsed.body === 'string' ? parsed.body : '',
    }
  } catch {
    return EMPTY_DRAFT
  }
}

function saveDraft(principalId: number, parkId: number, draft: ReportDraft) {
  try {
    const key = reportDraftKey(principalId, parkId)
    const hasContent = draft.activeForm !== 'question'
      || Boolean(draft.trackerKey || draft.title || draft.body)
    if (hasContent) localStorage.setItem(key, JSON.stringify(draft))
    else localStorage.removeItem(key)
  } catch {
    // Browser storage is optional; the in-memory form remains usable.
  }
}

function trackerUrlFromKey(key: string): string | null {
  const trimmed = key.trim()
  return trimmed ? `https://st.yandex-team.ru/${trimmed}` : null
}

export function ReportForms({ apiClient = api, ownerKey, parkId, principalId, restoreDraft = true, onCreated }: ReportFormsProps) {
  const [initial] = useState(() => restoreDraft ? loadDraft(principalId, parkId) : EMPTY_DRAFT)
  const [activeForm, setActiveForm] = useState<FormKind>(initial.activeForm)
  const [trackerKey, setTrackerKey] = useState(initial.trackerKey)
  const [title, setTitle] = useState(initial.title)
  const [body, setBody] = useState(initial.body)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [createdReportId, setCreatedReportId] = useState<number | null>(null)
  const [attachment, setAttachment] = useState<File | null>(null)
  const [attachmentKind, setAttachmentKind] = useState<ReportAttachmentKind>('device_photo')
  const [attachmentError, setAttachmentError] = useState('')
  const [attachmentSuccess, setAttachmentSuccess] = useState('')
  const [attaching, setAttaching] = useState(false)
  const ownerRef = useRef({ key: ownerKey, parkId, principalId })
  const generationRef = useRef(0)
  const mountedRef = useRef(true)

  useLayoutEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      generationRef.current += 1
    }
  }, [])

  useLayoutEffect(() => {
    if (ownerRef.current.key === ownerKey) return
    try {
      localStorage.removeItem(reportDraftKey(ownerRef.current.principalId, ownerRef.current.parkId))
    } catch {
      // Browser storage is optional.
    }
    ownerRef.current = { key: ownerKey, parkId, principalId }
    generationRef.current += 1
    const next = loadDraft(principalId, parkId)
    setActiveForm(next.activeForm)
    setTrackerKey(next.trackerKey)
    setTitle(next.title)
    setBody(next.body)
    setError('')
    setSuccess('')
    setSubmitting(false)
    setCreatedReportId(null)
    setAttachment(null)
    setAttachmentKind('device_photo')
    setAttachmentError('')
    setAttachmentSuccess('')
    setAttaching(false)
  }, [ownerKey, parkId, principalId])

  useEffect(() => {
    if (ownerRef.current.key !== ownerKey) return
    saveDraft(principalId, parkId, { activeForm, trackerKey, title, body })
  }, [activeForm, body, ownerKey, parkId, principalId, title, trackerKey])

  function resetFields() {
    setActiveForm('question')
    setTrackerKey('')
    setTitle('')
    setBody('')
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setSuccess('')

    const trimmedTitle = title.trim()
    if (!trimmedTitle) {
      setError('Укажите заголовок.')
      return
    }

    const kind: ReportKindManual =
      activeForm === 'question' ? 'ticket_question' : 'mechanic_problem'
    const trimmedKey = trackerKey.trim()

    if (activeForm === 'question' && !trimmedKey) {
      setError('Для вопроса по тикету укажите ключ Tracker.')
      return
    }

    setSubmitting(true)
    const generation = generationRef.current
    const requestedOwner = ownerKey
    const current = () => mountedRef.current
      && generationRef.current === generation
      && ownerRef.current.key === requestedOwner
    try {
      const created = await apiClient.createReport({
        kind,
        park_id: parkId,
        title: trimmedTitle,
        body: body.trim(),
        tracker_key: trimmedKey || null,
        tracker_url: trackerUrlFromKey(trimmedKey),
      })
      if (!current()) return
      setCreatedReportId(created.id)
      setSuccess('Репорт отправлен оператору.')
      resetFields()
      onCreated()
    } catch (submitError) {
      if (!current()) return
      setError(mapApiError(submitError, ru.errors.generic))
    } finally {
      if (current()) setSubmitting(false)
    }
  }

  async function attachFile() {
    if (createdReportId == null || attachment == null) return
    const photo = attachmentKind === 'device_photo' || attachmentKind === 'ui_snapshot'
    const imageTypes = new Set(['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/heic', 'image/heif'])
    const filename = attachment.name.toLowerCase()
    const fallbackMime = attachment.type === ''
      || attachment.type === 'application/octet-stream'
      || attachment.type === 'binary/octet-stream'
    const validType = photo
      ? imageTypes.has(attachment.type) || fallbackMime
      : attachment.type === 'text/plain' || (fallbackMime && filename.endsWith('.log'))
    const maxBytes = attachmentKind === 'client_log' ? 64 * 1024 : 15 * 1024 * 1024
    if (!validType) {
      setAttachmentError(photo ? 'Для снимка или фото нужен JPEG, PNG, WebP, HEIC или HEIF.' : 'Для лога нужен текстовый .log файл.')
      return
    }
    if (attachment.size > maxBytes) {
      setAttachmentError(`Файл превышает лимит ${attachmentKind === 'client_log' ? '64 КиБ' : '15 МиБ'}.`)
      return
    }
    setAttaching(true)
    setAttachmentError('')
    setAttachmentSuccess('')
    const generation = generationRef.current
    const requestedOwner = ownerKey
    const current = () => mountedRef.current
      && generationRef.current === generation
      && ownerRef.current.key === requestedOwner
    try {
      await apiClient.reportAttach(createdReportId, attachmentKind, attachment)
      if (!current()) return
      setAttachment(null)
      setAttachmentSuccess('Файл прикреплён к созданному репорту.')
    } catch (caught) {
      if (!current()) return
      setAttachmentError(mapApiError(caught, ru.errors.generic))
    } finally {
      if (current()) setAttaching(false)
    }
  }

  const canSubmit =
    Boolean(title.trim())
    && (activeForm === 'problem' || Boolean(trackerKey.trim()))

  return (
    <div className="reports-forms">
      <div className="actions">
        <button
          className={`btn btn-filter ${activeForm === 'question' ? 'is-active' : ''}`}
          onClick={() => setActiveForm('question')}
          type="button"
        >
          {ru.reports.actions.question}
        </button>
        <button
          className={`btn btn-filter ${activeForm === 'problem' ? 'is-active' : ''}`}
          onClick={() => setActiveForm('problem')}
          type="button"
        >
          {ru.reports.actions.problem}
        </button>
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      {createdReportId != null && (
        <div className="form-grid" aria-label="Вложения к репорту">
          <p className="panel-hint">По одному файлу каждого типа: снимок/фото JPEG, PNG, WebP, HEIC, HEIF до 15 МиБ или лог .log до 64 КиБ. Повтор не создаст новый репорт.</p>
          {attachmentError && <Alert tone="error">{attachmentError}</Alert>}
          {attachmentSuccess && <Alert tone="success">{attachmentSuccess}</Alert>}
          <label className="field">
            <span className="field-label">Файл</span>
            <input
              accept="image/jpeg,image/jpg,image/png,image/webp,image/heic,image/heif,application/octet-stream,binary/octet-stream,.jpg,.jpeg,.png,.webp,.heic,.heif,text/plain,.log"
              onChange={(event) => setAttachment(event.target.files?.[0] ?? null)}
              type="file"
            />
          </label>
          <label className="field">
            <span className="field-label">Тип вложения</span>
            <select onChange={(event) => setAttachmentKind(event.target.value as ReportAttachmentKind)} value={attachmentKind}>
              <option value="device_photo">Фото устройства</option>
              <option value="ui_snapshot">Снимок интерфейса</option>
              <option value="client_log">Лог клиента</option>
            </select>
          </label>
          <div className="form-actions">
            <button className="btn btn-secondary" disabled={attaching || attachment == null} onClick={() => void attachFile()} type="button">
              {attaching ? <Spinner label="Загрузка файла" /> : 'Прикрепить файл'}
            </button>
          </div>
        </div>
      )}

      <form className="form-grid" onSubmit={(event) => void handleSubmit(event)}>
        <p className="panel-hint">
          {activeForm === 'question'
            ? 'Вопрос по конкретному тикету — ключ Tracker обязателен.'
            : 'Опишите проблему; ссылку на тикет можно указать по желанию.'}
        </p>

        <label className="field">
          <span className="field-label">
            Ключ Tracker{activeForm === 'question' ? ' *' : ''}
          </span>
          <input
            onChange={(event) => setTrackerKey(event.target.value)}
            placeholder="ROBOPARK-123"
            required={activeForm === 'question'}
            value={trackerKey}
          />
        </label>

        <label className="field">
          <span className="field-label">Заголовок *</span>
          <input
            onChange={(event) => setTitle(event.target.value)}
            required
            value={title}
          />
        </label>

        <label className="field">
          <span className="field-label">Описание</span>
          <textarea
            onChange={(event) => setBody(event.target.value)}
            rows={4}
            value={body}
          />
        </label>

        <div className="form-actions">
          <button className="btn" disabled={submitting || !canSubmit} type="submit">
            {submitting ? <Spinner label={ru.create} /> : ru.create}
          </button>
        </div>
      </form>
    </div>
  )
}
