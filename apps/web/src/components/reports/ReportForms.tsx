import { type FormEvent, useState } from 'react'
import { api, type ReportKindManual } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Alert } from '../PageShell'

type ReportFormsProps = {
  parkId: number
  onCreated: () => void
}

type FormKind = 'question' | 'problem'

function trackerUrlFromKey(key: string): string | null {
  const trimmed = key.trim()
  return trimmed ? `https://st.yandex-team.ru/${trimmed}` : null
}

export function ReportForms({ parkId, onCreated }: ReportFormsProps) {
  const [activeForm, setActiveForm] = useState<FormKind>('question')
  const [trackerKey, setTrackerKey] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  function resetFields() {
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
    try {
      await api.createReport({
        kind,
        park_id: parkId,
        title: trimmedTitle,
        body: body.trim(),
        tracker_key: trimmedKey || null,
        tracker_url: trackerUrlFromKey(trimmedKey),
      })
      setSuccess('Репорт отправлен оператору.')
      resetFields()
      onCreated()
    } catch (submitError) {
      setError(mapApiError(submitError, ru.errors.generic))
    } finally {
      setSubmitting(false)
    }
  }

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

      <form className="panel" onSubmit={(event) => void handleSubmit(event)}>
        <p className="panel-hint">
          {activeForm === 'question'
            ? 'Вопрос по конкретному тикету — ключ Tracker обязателен.'
            : 'Опишите проблему; ссылку на тикет можно указать по желанию.'}
        </p>

        {(activeForm === 'question' || activeForm === 'problem') && (
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
        )}

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

        <div className="action-row">
          <button disabled={submitting} type="submit">
            {ru.create}
          </button>
        </div>
      </form>
    </div>
  )
}
