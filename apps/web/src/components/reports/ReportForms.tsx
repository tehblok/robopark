import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type ReportAttachmentKind, type ReportKindManual } from '../../api'
import { deleteReportPhotoDraft, quarantineReportPhotoDraft, restoreReportPhotoDraft, readReportPhotoDraft, writeReportPhotoDraft, type ReportPhotoDraft } from '../../domains/reports/reportPhotoDrafts'
import { reportDraftKey, type ReportsApiClient } from '../../domains/reports/reports'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Alert } from '../PageShell'
import { Spinner } from '../ui/Feedback'
import { FileField } from '../../design-system/inputs/FileField'

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
  createdReportId?: number | null
  ownerKey?: string
  attachmentDelivered?: boolean
}

const EMPTY_DRAFT: ReportDraft = {
  activeForm: 'question',
  trackerKey: '',
  title: '',
  body: '',
}

function loadDraft(principalId: number, parkId: number, ownerKey: string): ReportDraft {
  try {
    const stored = localStorage.getItem(reportDraftKey(principalId, parkId))
    if (!stored) return EMPTY_DRAFT
    const parsed = JSON.parse(stored) as Partial<ReportDraft>
    if (parsed.ownerKey !== ownerKey) return EMPTY_DRAFT
    return {
      activeForm: parsed.activeForm === 'problem' ? 'problem' : 'question',
      trackerKey: typeof parsed.trackerKey === 'string' ? parsed.trackerKey : '',
      title: typeof parsed.title === 'string' ? parsed.title : '',
      body: typeof parsed.body === 'string' ? parsed.body : '',
      attachmentDelivered: parsed.attachmentDelivered === true,
      createdReportId: parsed.ownerKey === ownerKey && typeof parsed.createdReportId === 'number' ? parsed.createdReportId : null,
    }
  } catch {
    return EMPTY_DRAFT
  }
}

function saveDraft(principalId: number, parkId: number, draft: ReportDraft) {
  try {
    const key = reportDraftKey(principalId, parkId)
    const hasContent = draft.activeForm !== 'question'
      || Boolean(draft.trackerKey || draft.title || draft.body || draft.createdReportId)
    if (hasContent) localStorage.setItem(key, JSON.stringify(draft))
    else localStorage.removeItem(key)
  } catch {
    throw new Error('Не удалось сохранить текст черновика.')
  }
}

function trackerUrlFromKey(key: string): string | null {
  const trimmed = key.trim()
  return trimmed ? `https://st.yandex-team.ru/${trimmed}` : null
}

export function ReportForms({ apiClient = api, ownerKey, parkId, principalId, restoreDraft = true, onCreated }: ReportFormsProps) {
  const [initial] = useState(() => restoreDraft ? loadDraft(principalId, parkId, ownerKey) : EMPTY_DRAFT)
  const [activeForm, setActiveForm] = useState<FormKind>(initial.activeForm)
  const [trackerKey, setTrackerKey] = useState(initial.trackerKey)
  const [title, setTitle] = useState(initial.title)
  const [body, setBody] = useState(initial.body)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [createdReportId, setCreatedReportId] = useState<number | null>(initial.createdReportId ?? null)
  const [attachmentDelivered, setAttachmentDelivered] = useState(initial.attachmentDelivered === true)
  const [attachment, setAttachment] = useState<File | null>(null)
  const [attachmentRestoreFailed, setAttachmentRestoreFailed] = useState(false)
  const [attachmentKind, setAttachmentKind] = useState<ReportAttachmentKind>('device_photo')
  const [attachmentError, setAttachmentError] = useState('')
  const [attachmentSuccess, setAttachmentSuccess] = useState('')
  const [attaching, setAttaching] = useState(false)
  const [draftReady, setDraftReady] = useState(false)
  const [storageError, setStorageError] = useState('')
  const [draftSaved, setDraftSaved] = useState(false)
  const editRef = useRef(0)
  const revisionRef = useRef('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const liveRef = useRef({ activeForm, trackerKey, title, body, attachment, attachmentKind })
  liveRef.current = { activeForm, trackerKey, title, body, attachment, attachmentKind }
  const busyRef = useRef(false)
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
    if (ownerRef.current.key === ownerKey && ownerRef.current.parkId === parkId && ownerRef.current.principalId === principalId) return
    void quarantineReportPhotoDraft(reportDraftKey(ownerRef.current.principalId, ownerRef.current.parkId), ownerRef.current.key).catch(() => {})
    try {
      localStorage.removeItem(reportDraftKey(ownerRef.current.principalId, ownerRef.current.parkId))
    } catch {
      // Browser storage is optional.
    }
    ownerRef.current = { key: ownerKey, parkId, principalId }
    generationRef.current += 1
    const next = restoreDraft ? loadDraft(principalId, parkId, ownerKey) : EMPTY_DRAFT
    editRef.current += 1
    setDraftReady(false)
    setStorageError('')
    setDraftSaved(false)
    busyRef.current = false
    setActiveForm(next.activeForm)
    setTrackerKey(next.trackerKey)
    setTitle(next.title)
    setBody(next.body)
    setError('')
    setSuccess('')
    setSubmitting(false)
    setCreatedReportId(next.createdReportId ?? null)
    setAttachmentDelivered(next.attachmentDelivered === true)
    setAttachment(null)
    setAttachmentRestoreFailed(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
    setAttachmentKind('device_photo')
    setAttachmentError('')
    setAttachmentSuccess('')
    setAttaching(false)
  }, [ownerKey, parkId, principalId, restoreDraft])

  useEffect(() => {
    const generation = generationRef.current
    const edits = editRef.current
    let cancelled = false
    const current = () => !cancelled && mountedRef.current && generationRef.current === generation && ownerRef.current.key === ownerKey
    const key = reportDraftKey(principalId, parkId)
    const loading = restoreDraft ? restoreReportPhotoDraft(key, ownerKey).then(() => readReportPhotoDraft(key)) : Promise.resolve(null)
    void loading.then(async (stored) => {
      if (!current()) return
      if (stored && stored.ownerKey === ownerKey && editRef.current === edits) {
        const textDraft = loadDraft(principalId, parkId, ownerKey)
        const savedAttachment = !textDraft.attachmentDelivered ? stored.attachment : null
        // Rewriting an IndexedDB-backed Blob can leave WebKit's native multipart
        // stream unreadable after reload. Restore one independently owned File.
        let restoredAttachment: File | null = null
        let photoReadFailed = false
        try {
          if (savedAttachment) {
            const bytes = await savedAttachment.blob.arrayBuffer()
            if (!current() || editRef.current !== edits) return
            restoredAttachment = new File([bytes], savedAttachment.name, {
              type: savedAttachment.blob.type, lastModified: savedAttachment.lastModified,
            })
          }
        } catch { photoReadFailed = true }
        if (!current() || editRef.current !== edits) return
        setAttachmentRestoreFailed(photoReadFailed)
        if (photoReadFailed) setStorageError('Не удалось прочитать сохранённый файл. Данные репорта восстановлены; выберите файл заново или повторите после перезагрузки.')
        revisionRef.current = stored.revision
        const hasSynchronousText = Boolean(textDraft.title || textDraft.body || textDraft.trackerKey || textDraft.createdReportId)
        // Synchronous text is the newest fallback if the browser closed mid-transaction.
        setActiveForm(hasSynchronousText ? textDraft.activeForm : stored.activeForm)
        setTrackerKey(hasSynchronousText ? textDraft.trackerKey : stored.trackerKey)
        setTitle(hasSynchronousText ? textDraft.title : stored.title)
        setBody(hasSynchronousText ? textDraft.body : stored.body)
        setCreatedReportId((knownId) => knownId ?? textDraft.createdReportId ?? stored.createdReportId)
        setAttachmentKind(stored.attachmentKind)
        setAttachment(restoredAttachment)
      }
    }).catch(() => {
      if (current()) setStorageError('Не удалось восстановить черновик с файлами. Текст доступен; не закрывайте страницу до отправки.')
    }).finally(() => { if (current()) setDraftReady(true) })
    return () => { cancelled = true }
  }, [ownerKey, parkId, principalId, restoreDraft])

  useEffect(() => {
    if (ownerRef.current.key !== ownerKey || !draftReady) return
    try {
      saveDraft(principalId, parkId, { activeForm, trackerKey, title, body, ownerKey, attachmentDelivered, createdReportId: attachment || attachmentDelivered || attachmentRestoreFailed ? createdReportId : null })
    } catch {
      setStorageError('Не удалось сохранить черновик на устройстве. Форма и файл остаются доступны; не закрывайте страницу до отправки.')
    }
    // Keep the original IDB record available for reload/recovery. A failed read
    // must not overwrite its attachment or acknowledged report ID with empties.
    if (attachmentRestoreFailed) return
    const revision = crypto.randomUUID()
    const previousRevision = revisionRef.current
    revisionRef.current = revision
    const stored: ReportPhotoDraft = {
      key: reportDraftKey(principalId, parkId), ownerKey, revision,
      activeForm, trackerKey, title, body, createdReportId, attachmentKind,
      attachment: attachment ? { blob: attachment, name: attachment.name, lastModified: attachment.lastModified } : null,
    }
    const generation = generationRef.current
    const current = () => mountedRef.current && generationRef.current === generation && ownerRef.current.key === ownerKey && revisionRef.current === revision
    setDraftSaved(false)
    const hasContent = activeForm !== 'question' || trackerKey || title || body || attachment
    const saving = hasContent ? writeReportPhotoDraft(stored) : deleteReportPhotoDraft(stored.key, previousRevision)
    void saving.then(() => {
      if (current()) { setDraftSaved(Boolean(hasContent)); setStorageError(''); if (attachmentDelivered) setAttachmentDelivered(false) }
    }).catch((caught: unknown) => {
      if (current()) setStorageError(`Не удалось сохранить черновик на устройстве. ${caught instanceof Error && caught.name !== 'QuotaExceededError' ? caught.message : 'Хранилище заполнено.'} Форма и файл остаются доступны; не закрывайте страницу до отправки.`)
    })
  }, [activeForm, attachment, attachmentDelivered, attachmentKind, attachmentRestoreFailed, body, createdReportId, draftReady, ownerKey, parkId, principalId, title, trackerKey])

  function edited() { editRef.current += 1; setDraftSaved(false) }

  function discardDraft() {
    edited()
    resetFields()
    setCreatedReportId(null)
    setAttachmentDelivered(false)
    setAttachment(null)
    setAttachmentRestoreFailed(false)
    setAttachmentKind('device_photo')
    setSuccess('')
    setAttachmentSuccess('')
    setAttachmentError('')
    setStorageError('')
    if (fileInputRef.current) fileInputRef.current.value = ''
    void deleteReportPhotoDraft(reportDraftKey(principalId, parkId)).catch(() => {
      if (mountedRef.current && ownerRef.current.key === ownerKey) setStorageError('Не удалось удалить черновик с устройства.')
    })
  }

  function resetFields() {
    setActiveForm('question')
    setTrackerKey('')
    setTitle('')
    setBody('')
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (busyRef.current || createdReportId != null || !draftReady) return
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

    busyRef.current = true
    setSubmitting(true)
    const submitted = liveRef.current
    const submittedRevision = revisionRef.current
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
      const unchanged = liveRef.current.title === submitted.title && liveRef.current.body === submitted.body
        && liveRef.current.trackerKey === submitted.trackerKey && liveRef.current.activeForm === submitted.activeForm
      const remainingText = unchanged ? EMPTY_DRAFT : liveRef.current
      if (unchanged) resetFields()
      // Persist the acknowledged report ID before the separate attachment request.
      try {
        saveDraft(principalId, parkId, { activeForm: remainingText.activeForm, trackerKey: remainingText.trackerKey, title: remainingText.title, body: remainingText.body, ownerKey, createdReportId: attachment ? created.id : null })
      } catch {
        setStorageError('Репорт создан, но номер не сохранён на устройстве. Не закрывайте страницу до загрузки файла.')
      }
      if (!submitted.attachment) void deleteReportPhotoDraft(reportDraftKey(principalId, parkId), submittedRevision).catch(() => {})
      onCreated()
    } catch (submitError) {
      if (!current()) return
      setError(mapApiError(submitError, ru.errors.generic))
    } finally {
      if (current()) { setSubmitting(false); busyRef.current = false }
    }
  }

  async function attachFile() {
    if (busyRef.current || createdReportId == null || attachment == null) return
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
    busyRef.current = true
    const submittedAttachment = attachment
    const submittedKind = attachmentKind
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
      if (liveRef.current.attachment === submittedAttachment && liveRef.current.attachmentKind === submittedKind) {
        // A small synchronous receipt prevents resurrection if photo deletion fails.
        setAttachmentDelivered(true)
        try {
          saveDraft(principalId, parkId, { activeForm: liveRef.current.activeForm, trackerKey: liveRef.current.trackerKey, title: liveRef.current.title, body: liveRef.current.body, ownerKey, createdReportId, attachmentDelivered: true })
        } catch { setStorageError('Файл отправлен, но подтверждение не сохранено на устройстве.') }
        setAttachment(null)
        setAttachmentRestoreFailed(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
      setAttachmentSuccess('Файл прикреплён к созданному репорту.')
    } catch (caught) {
      if (!current()) return
      setAttachmentError(mapApiError(caught, ru.errors.generic))
    } finally {
      if (current()) { setAttaching(false); busyRef.current = false }
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
          disabled={submitting || attaching || !draftReady}
          onClick={() => { edited(); setActiveForm('question') }}
          type="button"
        >
          {ru.reports.actions.question}
        </button>
        <button
          className={`btn btn-filter ${activeForm === 'problem' ? 'is-active' : ''}`}
          disabled={submitting || attaching || !draftReady}
          onClick={() => { edited(); setActiveForm('problem') }}
          type="button"
        >
          {ru.reports.actions.problem}
        </button>
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      {storageError && <Alert tone="error">{storageError}</Alert>}
      {draftSaved && !storageError && <p className="panel-hint" role="status">Черновик сохранён на этом устройстве.</p>}
      {createdReportId != null && <p className="panel-hint">Репорт №{createdReportId} уже создан. Можно завершить загрузку файла.</p>}


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
            disabled={!draftReady}
            onChange={(event) => { edited(); setTrackerKey(event.target.value) }}
            placeholder="ROBOPARK-123"
            required={activeForm === 'question'}
            value={trackerKey}
          />
        </label>

        <label className="field">
          <span className="field-label">Заголовок *</span>
          <input
            disabled={!draftReady}
            onChange={(event) => { edited(); setTitle(event.target.value) }}
            required
            value={title}
          />
        </label>

        <label className="field">
          <span className="field-label">Описание</span>
          <textarea
            disabled={!draftReady}
            onChange={(event) => { edited(); setBody(event.target.value) }}
            rows={4}
            value={body}
          />
        </label>

      <div className="form-grid" aria-label={createdReportId != null ? 'Вложения к репорту' : 'Файл черновика'}>
          <p className="panel-hint">По одному файлу каждого типа: снимок/фото JPEG, PNG, WebP, HEIC, HEIF до 15 МиБ или лог .log до 64 КиБ. Повтор не создаст новый репорт.</p>
          {attachmentError && <Alert tone="error">{attachmentError}</Alert>}
          {attachmentSuccess && <Alert tone="success">{attachmentSuccess}</Alert>}
          <FileField
            accept="image/jpeg,image/jpg,image/png,image/webp,image/heic,image/heif,application/octet-stream,binary/octet-stream,.jpg,.jpeg,.png,.webp,.heic,.heif,text/plain,.log"
            disabled={submitting || attaching || !draftReady}
            inputRef={fileInputRef}
            label="Файл"
            onChange={(event) => { edited(); setAttachmentRestoreFailed(false); setAttachmentDelivered(false); setAttachment(event.target.files?.[0] ?? null) }}
            selectedFileLabel={attachment ? `Выбран файл: ${attachment.name} (${Math.ceil(attachment.size / 1024)} КиБ)` : null}
          />
          <label className="field">
            <span className="field-label">Тип вложения</span>
            <select disabled={submitting || attaching || !draftReady} onChange={(event) => { edited(); setAttachmentKind(event.target.value as ReportAttachmentKind) }} value={attachmentKind}>
              <option value="device_photo">Фото устройства</option>
              <option value="ui_snapshot">Снимок интерфейса</option>
              <option value="client_log">Лог клиента</option>
            </select>
          </label>
          <div className="form-actions">
            <button className="btn btn-secondary" disabled={submitting || attaching || attachment == null || createdReportId == null} onClick={() => void attachFile()} type="button">
              {attaching ? <Spinner label="Загрузка файла" /> : 'Прикрепить файл'}
            </button>
          </div>
        </div>

        <div className="form-actions">
          <button className="btn" disabled={submitting || attaching || !draftReady || createdReportId != null || !canSubmit} type="submit">
            {submitting ? <Spinner label={ru.create} /> : ru.create}
          </button>
          <button className="btn btn-secondary" disabled={submitting || attaching} onClick={discardDraft} type="button">Удалить черновик</button>
          {createdReportId != null && !attachment && !attachmentRestoreFailed && <button className="btn btn-secondary" onClick={() => { edited(); setAttachmentDelivered(false); setCreatedReportId(null); setSuccess(''); setAttachmentSuccess('') }} type="button">Новый репорт</button>}
        </div>
      </form>
    </div>
  )
}
