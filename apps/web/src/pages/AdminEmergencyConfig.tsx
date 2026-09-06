import { type FormEvent, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { Tabs, TabPanel } from '../design-system/navigation/Tabs'
import { DiagnosticRuleEditor } from '../domains/diagnostics/DiagnosticRuleEditor'
import {
  api,
  type EmergencyAdminSection,
  type EmergencyViewerRole,
} from '../api'
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { Toggle } from '../components/ui/Tabs'
import { mapApiError } from '../i18n/errors'
import { roleLabel, ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'

const roles: EmergencyViewerRole[] = ['mechanic', 'operator', 'admin', 'royal', 'driver']

export function AdminEmergencyConfig() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const canEditRules = user?.access_status === 'approved' && ['admin', 'royal'].includes(user.role)
  const tab = canEditRules && params.get('tab') === 'indication' ? 'indication' : 'fields'
  return <PageShell backTo="/admin" title="Настройки проверки робота" subtitle="Разделы диагностики и общие для всех парков правила ошибок.">
    <div className="rp-check-settings-tabs"><Tabs ariaLabel="Настройки проверки робота" value={tab} panelIdFor={id => `check-settings-${id}`}
      items={[{ id: 'fields', label: 'Разделы и поля' }, ...(canEditRules ? [{ id: 'indication', label: 'Ошибки и индикация' }] : [])]}
      onChange={id => { const next = new URLSearchParams(params); next.set('tab', id); next.delete('rule'); setParams(next) }} /></div>
    <TabPanel id={`check-settings-${tab}`} labelledBy={`tab-${tab}`} active>
      {tab === 'indication' ? <DiagnosticRuleEditor /> : <EmergencyFieldsConfig />}
    </TabPanel>
  </PageShell>
}

function EmergencyFieldsConfig() {
  const sectionsRes = useCachedResource<EmergencyAdminSection[]>(
    'admin:emergency-sections',
    () => api.adminEmergencySections(),
    // Keep editable section drafts until an explicit save or retry.
    { refreshIntervalMs: 0 },
  )
  const cached = sectionsRes.data
  const [sections, setSections] = useState<EmergencyAdminSection[]>(cached ?? [])
  const [sectionId, setSectionId] = useState('')
  const [sectionTitle, setSectionTitle] = useState('')
  const [newFields, setNewFields] = useState<Record<string, { path: string; label: string }>>({})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (cached) setSections(cached)
  }, [cached])

  const run = async (
    action: () => Promise<unknown>,
    success = 'Изменения сохранены.',
    reloadOnFailure = false,
  ) => {
    setError('')
    setMessage('')
    setBusy(true)
    try {
      await action()
      await sectionsRes.refresh()
      setMessage(success)
    } catch {
      if (reloadOnFailure) {
        await sectionsRes.refresh().catch(() => undefined)
      }
      setError(ru.errors.generic)
    } finally {
      setBusy(false)
    }
  }

  const editSection = (id: string, changes: Partial<EmergencyAdminSection>) => {
    setSections((current) => current.map((section) => (
      section.id === id ? { ...section, ...changes } : section
    )))
  }

  const editField = (
    sectionIdToEdit: string,
    fieldId: number,
    changes: { path?: string; label?: string },
  ) => {
    setSections((current) => current.map((section) => (
      section.id === sectionIdToEdit
        ? {
            ...section,
            fields: section.fields.map((field) => (
              field.id === fieldId ? { ...field, ...changes } : field
            )),
          }
        : section
    )))
  }

  const createSection = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createEmergencySection({
        id: sectionId.trim(),
        title: sectionTitle.trim(),
        roles: [...roles],
      })
      setSectionId('')
      setSectionTitle('')
    }, 'Раздел создан.')
  }

  const setRole = (section: EmergencyAdminSection, role: EmergencyViewerRole, next: boolean) => {
    const nextRoles = next
      ? [...new Set([...section.roles, role])]
      : section.roles.filter((item) => item !== role)
    editSection(section.id, { roles: nextRoles })
  }

  const moveSection = (index: number, offset: -1 | 1) => {
    const target = index + offset
    if (target < 0 || target >= sections.length) return
    const reordered = [...sections]
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    setSections(reordered)
    void run(
      () => api.reorderEmergencySections(reordered.map((section) => section.id)),
      'Изменения сохранены.',
      true,
    )
  }

  const saveSection = (section: EmergencyAdminSection) => run(() =>
    api.updateEmergencySection(section.id, {
      title: section.title.trim(),
      is_enabled: section.is_enabled,
      roles: section.roles,
    }))

  const addField = async (event: FormEvent, id: string) => {
    event.preventDefault()
    const draft = newFields[id]
    if (!draft?.path.trim() || !draft.label.trim()) return
    await run(async () => {
      await api.createEmergencyField(id, {
        path: draft.path.trim(),
        label: draft.label.trim(),
      })
      setNewFields((current) => ({
        ...current,
        [id]: { path: '', label: '' },
      }))
    }, 'Поле добавлено.')
  }

  const downloadExport = async () => {
    setError('')
    setMessage('')
    try {
      const blob = await api.exportEmergencyConfig()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'emergency-sections.json'
      link.click()
      URL.revokeObjectURL(url)
      setMessage('Конфиг скачан.')
    } catch {
      setError(ru.errors.generic)
    }
  }

  const displayError = error || (sectionsRes.error ? mapApiError(sectionsRes.error, ru.errors.load) : '')
  const showColdSkeleton = sectionsRes.isLoading && !cached

  if (showColdSkeleton) {
    return <SkeletonList rows={4} />
  }

  return (
    <div className="page-body">
      <div className="form-actions">
        <button className="btn btn-secondary" onClick={downloadExport} type="button">
          Скачать JSON
        </button>
      </div>
      {displayError && <Alert tone="error">{displayError}</Alert>}
      {message && <Alert tone="success">{message}</Alert>}

      <Panel hint="ID можно задать только при создании." title="Новый раздел">
        <form className="form-grid" onSubmit={createSection}>
          <label className="field">
            <span className="field-label">ID раздела</span>
            <input
              onChange={(event) => setSectionId(event.target.value)}
              pattern="[A-Za-z0-9_-]+"
              placeholder="robot_state"
              required
              value={sectionId}
            />
          </label>
          <label className="field">
            <span className="field-label">Название</span>
            <input
              onChange={(event) => setSectionTitle(event.target.value)}
              placeholder="Состояние робота"
              required
              value={sectionTitle}
            />
          </label>
          <div className="form-actions">
            <button className="btn" disabled={busy} type="submit">
              {ru.create}
            </button>
          </div>
        </form>
      </Panel>

      {sections.length === 0 ? (
        <EmptyBlock
          hint="Разделы задают, какие диагностические поля видят роли."
          icon="⚑"
          title="Разделы проверки робота ещё не настроены"
        />
      ) : (
        sections.map((section, index) => {
          const draft = newFields[section.id] ?? { path: '', label: '' }
          const canAddField = Boolean(draft.path.trim() && draft.label.trim())
          return (
            <Panel
              actions={(
                <>
                  <Badge active={section.is_enabled} />
                  <button
                    aria-label="Выше"
                    className="btn btn-ghost"
                    disabled={busy || index === 0}
                    onClick={() => moveSection(index, -1)}
                    type="button"
                  >
                    ↑
                  </button>
                  <button
                    aria-label="Ниже"
                    className="btn btn-ghost"
                    disabled={busy || index === sections.length - 1}
                    onClick={() => moveSection(index, 1)}
                    type="button"
                  >
                    ↓
                  </button>
                  <button
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() => {
                      if (window.confirm(`Удалить раздел «${section.title}»?`)) {
                        void run(() => api.deleteEmergencySection(section.id), 'Раздел удалён.')
                      }
                    }}
                    type="button"
                  >
                    Удалить
                  </button>
                </>
              )}
              hint={`ID: ${section.id} · позиция ${index + 1}`}
              key={section.id}
              title={section.title || section.id}
            >
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">Название</span>
                  <input
                    aria-label={`Название ${section.id}`}
                    onChange={(event) => editSection(section.id, { title: event.target.value })}
                    required
                    value={section.title}
                  />
                </label>
              </div>

              <div className="toggle-list emergency-config-section-toggles">
                <Toggle
                  checked={section.is_enabled}
                  label="Раздел включён"
                  onChange={(next) => editSection(section.id, { is_enabled: next })}
                />
                {roles.map((role) => (
                  <Toggle
                    checked={section.roles.includes(role)}
                    key={role}
                    label={roleLabel(role)}
                    onChange={(next) => setRole(section, role, next)}
                  />
                ))}
              </div>

              <div className="emergency-config-fields">
                <span className="field-label">Поля</span>
                {section.fields.map((field) => (
                  <div className="form-grid emergency-config-field-row" key={field.id}>
                    <label className="field">
                      <span className="field-label">Путь</span>
                      <input
                        aria-label={`Путь поля ${field.id}`}
                        onChange={(event) => editField(section.id, field.id, {
                          path: event.target.value,
                        })}
                        value={field.path}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">Подпись</span>
                      <input
                        aria-label={`Подпись поля ${field.id}`}
                        onChange={(event) => editField(section.id, field.id, {
                          label: event.target.value,
                        })}
                        value={field.label}
                      />
                    </label>
                    <div className="form-actions">
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={() => run(() => api.updateEmergencyField(field.id, {
                          path: field.path.trim(),
                          label: field.label.trim(),
                        }), 'Поле сохранено.')}
                        type="button"
                      >
                        {ru.save}
                      </button>
                      <button
                        className="btn btn-secondary"
                        disabled={busy}
                        onClick={() => run(
                          () => api.deleteEmergencyField(field.id),
                          'Поле удалено.',
                        )}
                        type="button"
                      >
                        Удалить
                      </button>
                    </div>
                  </div>
                ))}

                <form
                  className="form-grid emergency-config-field-row"
                  onSubmit={(event) => addField(event, section.id)}
                >
                  <label className="field">
                    <span className="field-label">Путь</span>
                    <input
                      aria-label={`Путь нового поля ${section.id}`}
                      onChange={(event) => setNewFields((current) => ({
                        ...current,
                        [section.id]: {
                          label: current[section.id]?.label ?? '',
                          path: event.target.value,
                        },
                      }))}
                      placeholder="data.status"
                      value={draft.path}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">Подпись</span>
                    <input
                      aria-label={`Подпись нового поля ${section.id}`}
                      onChange={(event) => setNewFields((current) => ({
                        ...current,
                        [section.id]: {
                          path: current[section.id]?.path ?? '',
                          label: event.target.value,
                        },
                      }))}
                      placeholder="Статус"
                      value={draft.label}
                    />
                  </label>
                  <div className="form-actions">
                    <button className="btn" disabled={busy || !canAddField} type="submit">
                      Добавить поле
                    </button>
                  </div>
                </form>
              </div>

              <div className="form-actions emergency-config-section-save">
                <button
                  className="btn"
                  disabled={busy || !section.title.trim()}
                  onClick={() => saveSection(section)}
                  type="button"
                >
                  {ru.save}
                </button>
              </div>
            </Panel>
          )
        })
      )}
    </div>
  )
}
