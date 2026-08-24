import { type FormEvent, useEffect, useState } from 'react'
import {
  api,
  type EmergencyAdminSection,
  type EmergencyViewerRole,
} from '../api'
import { useAuth } from '../auth-context'
import { Alert, Badge, EmptyState, PageShell, Panel } from '../components/PageShell'
import { roleLabel, ru } from '../i18n/ru'

const roles: EmergencyViewerRole[] = ['mechanic', 'operator', 'admin', 'royal']

export function AdminEmergencyConfig() {
  const { logout } = useAuth()
  const [sections, setSections] = useState<EmergencyAdminSection[]>([])
  const [sectionId, setSectionId] = useState('')
  const [sectionTitle, setSectionTitle] = useState('')
  const [newFields, setNewFields] = useState<Record<string, { path: string; label: string }>>({})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = async () => setSections(await api.adminEmergencySections())

  useEffect(() => {
    api.adminEmergencySections().then(setSections).catch(() => setError(ru.errors.load))
  }, [])

  const run = async (action: () => Promise<unknown>, success = 'Изменения сохранены.') => {
    setError('')
    setMessage('')
    try {
      await action()
      await load()
      setMessage(success)
    } catch {
      setError(ru.errors.generic)
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

  const toggleRole = (section: EmergencyAdminSection, role: EmergencyViewerRole) => {
    const nextRoles = section.roles.includes(role)
      ? section.roles.filter((item) => item !== role)
      : [...section.roles, role]
    editSection(section.id, { roles: nextRoles })
  }

  const moveSection = (index: number, offset: -1 | 1) => {
    const target = index + offset
    if (target < 0 || target >= sections.length) return
    const reordered = [...sections]
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    setSections(reordered)
    void run(() => api.reorderEmergencySections(reordered.map((section) => section.id)))
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
    if (!draft) return
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
    try {
      const blob = await api.exportEmergencyConfig()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'emergency-sections.json'
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      setError(ru.errors.generic)
    }
  }

  return (
    <PageShell
      backTo="/admin"
      onLogout={logout}
      subtitle="Разделы и поля, доступ по ролям, порядок и выгрузка конфигурации."
      title="Конфиг Emergency"
    >
      {error && <Alert tone="error">{error}</Alert>}
      {message && <Alert tone="success">{message}</Alert>}

      <Panel hint="ID можно задать только при создании." title="Новый раздел">
        <form className="inline-form" onSubmit={createSection}>
          <input
            aria-label="ID раздела"
            onChange={(event) => setSectionId(event.target.value)}
            pattern="[A-Za-z0-9_-]+"
            placeholder="robot_state"
            required
            value={sectionId}
          />
          <input
            aria-label="Название раздела"
            onChange={(event) => setSectionTitle(event.target.value)}
            placeholder="Состояние робота"
            required
            value={sectionTitle}
          />
          <button type="submit">{ru.create}</button>
          <button className="btn btn-secondary" onClick={downloadExport} type="button">
            Скачать JSON
          </button>
        </form>
      </Panel>

      <Panel hint="Порядок здесь определяет порядок разделов у пользователей." title="Разделы">
        {sections.length ? (
          <div className="emergency-config-list">
            {sections.map((section, index) => (
              <article className="emergency-config-section" key={section.id}>
                <div className="emergency-config-heading">
                  <div>
                    <strong>{section.id}</strong>
                    <div className="card-meta">
                      <Badge active={section.is_enabled} />
                      <span>Позиция {index + 1}</span>
                    </div>
                  </div>
                  <div className="actions">
                    <button
                      className="btn btn-secondary"
                      disabled={index === 0}
                      onClick={() => moveSection(index, -1)}
                      type="button"
                    >
                      ↑
                    </button>
                    <button
                      className="btn btn-secondary"
                      disabled={index === sections.length - 1}
                      onClick={() => moveSection(index, 1)}
                      type="button"
                    >
                      ↓
                    </button>
                  </div>
                </div>

                <div className="inline-form">
                  <input
                    aria-label={`Название ${section.id}`}
                    onChange={(event) => editSection(section.id, { title: event.target.value })}
                    required
                    value={section.title}
                  />
                  <label className="emergency-config-toggle">
                    <input
                      checked={section.is_enabled}
                      onChange={(event) => editSection(section.id, {
                        is_enabled: event.target.checked,
                      })}
                      type="checkbox"
                    />
                    Раздел включён
                  </label>
                </div>

                <div>
                  <span className="field-label">Доступные роли</span>
                  <div className="checks">
                    {roles.map((role) => (
                      <label key={role}>
                        <input
                          checked={section.roles.includes(role)}
                          onChange={() => toggleRole(section, role)}
                          type="checkbox"
                        />
                        {roleLabel(role)}
                      </label>
                    ))}
                  </div>
                </div>

                <div className="emergency-config-fields">
                  <span className="field-label">Поля</span>
                  {section.fields.map((field) => (
                    <div className="emergency-config-field" key={field.id}>
                      <input
                        aria-label={`Путь поля ${field.id}`}
                        onChange={(event) => editField(section.id, field.id, {
                          path: event.target.value,
                        })}
                        value={field.path}
                      />
                      <input
                        aria-label={`Подпись поля ${field.id}`}
                        onChange={(event) => editField(section.id, field.id, {
                          label: event.target.value,
                        })}
                        value={field.label}
                      />
                      <button
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
                        onClick={() => run(() => api.deleteEmergencyField(field.id), 'Поле удалено.')}
                        type="button"
                      >
                        Удалить
                      </button>
                    </div>
                  ))}
                  <form className="emergency-config-field" onSubmit={(event) => addField(event, section.id)}>
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
                      required
                      value={newFields[section.id]?.path ?? ''}
                    />
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
                      required
                      value={newFields[section.id]?.label ?? ''}
                    />
                    <button type="submit">Добавить поле</button>
                  </form>
                </div>

                <div className="actions">
                  <button disabled={!section.title.trim()} onClick={() => saveSection(section)} type="button">
                    {ru.save}
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => run(
                      () => api.updateEmergencySection(section.id, {
                        is_enabled: !section.is_enabled,
                      }),
                      section.is_enabled ? 'Раздел выключен.' : 'Раздел включён.',
                    )}
                    type="button"
                  >
                    {section.is_enabled ? ru.deactivate : ru.activate}
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => {
                      if (window.confirm(`Удалить раздел «${section.title}»?`)) {
                        void run(() => api.deleteEmergencySection(section.id), 'Раздел удалён.')
                      }
                    }}
                    type="button"
                  >
                    Удалить раздел
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState>Разделы Emergency ещё не настроены.</EmptyState>
        )}
      </Panel>
    </PageShell>
  )
}
