import { useEffect, useState } from 'react'
import { api, type AdminRole, type PermissionCatalogItem } from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, Panel } from '../PageShell'
import { Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { actorPermissionCatalog } from './privilegedPermissions'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { EntityRow } from '../../design-system/data/EntityRow'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { EffectivePermissions } from './EffectivePermissions'

export function AdminRolesPanel() {
  const { user } = useAuth()
  const [roles, setRoles] = useState<AdminRole[]>([])
  const [catalog, setCatalog] = useState<PermissionCatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [draft, setDraft] = useState<{ name: string; description: string; permissions: Set<string> }>(
    { name: '', description: '', permissions: new Set() },
  )
  const [newSlug, setNewSlug] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [roleRows, permCatalog] = await Promise.all([
        api.adminRoles(),
        api.adminRolePermissionCatalog(),
      ])
      setRoles(roleRows)
      setCatalog(permCatalog)
    } catch (loadError) {
      setError(mapApiError(loadError) || 'Не удалось загрузить роли')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const availableCatalog = actorPermissionCatalog(catalog, user?.role)
  const navPerms = availableCatalog.filter((item) => item.category === 'nav')
  const actionPerms = availableCatalog.filter((item) => item.category === 'action')

  const startEdit = (role: AdminRole) => {
    setDetailOpen(true)
    setEditingId(role.id)
    setDraft({
      name: role.name,
      description: role.description,
      permissions: new Set(role.permissions),
    })
    setNewSlug('')
  }

  const togglePerm = (key: string) => {
    setDraft((current) => {
      const next = new Set(current.permissions)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return { ...current, permissions: next }
    })
  }

  const saveRole = async () => {
    if (editingId === null) return
    setBusy(true)
    setError('')
    try {
      const updated = await api.updateAdminRole(editingId, {
        name: draft.name.trim(),
        description: draft.description.trim(),
        permissions: [...draft.permissions],
      })
      setRoles((rows) => rows.map((row) => (row.id === updated.id ? updated : row)))
    } catch (saveError) {
      setError(mapApiError(saveError) || 'Не удалось сохранить роль')
    } finally {
      setBusy(false)
    }
  }

  const createRole = async () => {
    const slug = newSlug.trim().toLowerCase()
    if (!slug || !draft.name.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createAdminRole({
        slug,
        name: draft.name.trim(),
        description: draft.description.trim(),
        permissions: [...draft.permissions],
      })
      setRoles((rows) => [...rows, created])
      setNewSlug('')
      setEditingId(created.id)
    } catch (createError) {
      setError(mapApiError(createError) || 'Не удалось создать роль')
    } finally {
      setBusy(false)
    }
  }

  const removeRole = async (role: AdminRole) => {
    if (role.is_system) return
    if (!window.confirm(`Удалить роль «${role.name}»?`)) return
    setBusy(true)
    try {
      await api.deleteAdminRole(role.id)
      setRoles((rows) => rows.filter((row) => row.id !== role.id))
      if (editingId === role.id) setEditingId(null)
    } catch (deleteError) {
      setError(mapApiError(deleteError) || 'Не удалось удалить роль')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <Spinner label="Загрузка ролей…" />
  }

  const editing = editingId != null ? roles.find((row) => row.id === editingId) : null

  return (
    <div className="admin-roles">
      {error && <Alert tone="error">{error}</Alert>}

      <MasterDetail detailOpen={detailOpen} onBack={() => setDetailOpen(false)} list={
      <Panel hint="Выберите роль для просмотра и изменения доступов." title="Роли"
        actions={<button className="btn btn-secondary" type="button" onClick={() => {
          setEditingId(null); setNewSlug(''); setDraft({ name: '', description: '', permissions: new Set() }); setDetailOpen(true)
        }}>Новая роль</button>}>
        <ul className="admin-role-list">
          {roles.map((role) => (
            <li key={role.id}>
              <button aria-label={`Открыть роль ${role.name}`} aria-pressed={editingId === role.id} className={`rp-management-select${editingId === role.id ? ' is-selected' : ''}`} onClick={() => startEdit(role)} type="button">
                <EntityRow title={role.name} meta={`${role.slug} · ${role.user_count} польз. · ${role.permissions.length} разрешений`}
                  status={<StatusBadge tone="neutral">{role.is_system ? 'Системная' : 'Пользовательская'}</StatusBadge>} />
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      } detail={
      <Panel title={editing ? `Редактор: ${editing.name}` : 'Новая роль'}>
        {!editing && (
          <label className="field">
            <span className="field-label">Slug (латиница)</span>
            <input onChange={(e) => setNewSlug(e.target.value)} value={newSlug} />
          </label>
        )}
        <label className="field">
          <span className="field-label">Название</span>
          <input
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            value={draft.name}
          />
        </label>
        <label className="field">
          <span className="field-label">Описание</span>
          <input
            onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            value={draft.description}
          />
        </label>

        <h4 className="admin-perm-group-title">Разделы меню</h4>
        <div className="admin-perm-grid">
          {navPerms.map((perm) => (
            <label className="admin-perm-check" key={perm.key}>
              <input
                checked={draft.permissions.has(perm.key)}
                onChange={() => togglePerm(perm.key)}
                type="checkbox"
              />
              {perm.label}
            </label>
          ))}
        </div>

        <h4 className="admin-perm-group-title">Действия</h4>
        <div className="admin-perm-grid">
          {actionPerms.map((perm) => (
            <label className="admin-perm-check" key={perm.key}>
              <input
                checked={draft.permissions.has(perm.key)}
                onChange={() => togglePerm(perm.key)}
                type="checkbox"
              />
              {perm.label}
            </label>
          ))}
        </div>

        <EffectivePermissions permissions={draft.permissions} catalog={catalog} />
        <div className="form-actions">
          {editing ? (
            <button className="btn" disabled={busy} onClick={() => void saveRole()} type="button">
              Сохранить
            </button>
          ) : (
            <button className="btn" disabled={busy} onClick={() => void createRole()} type="button">
              Создать роль
            </button>
          )}
        </div>
        {editing && !editing.is_system && (
          <section aria-label="Опасные действия" className="rp-danger-zone">
            <h3>Опасные действия</h3>
            <p>Перед удалением проверьте назначенные аккаунты.</p>
            <button className="btn btn-danger" disabled={busy} onClick={() => void removeRole(editing)} type="button">Удалить роль</button>
          </section>
        )}
      </Panel>
      } />
    </div>
  )
}
