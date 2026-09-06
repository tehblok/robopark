import { useEffect, useLayoutEffect, useState } from 'react'
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
import { resourceStore, useCachedResource } from '../../lib/resource'
import { adminAccessDeniedMessage, adminAccessFailure, adminResourceKey, adminResourceOptions } from './adminResources'

export function AdminRolesPanel() {
  const { user } = useAuth()
  return <AdminRolesScope key={adminResourceKey('workspace', user)} />
}

function AdminRolesScope() {
  const [denied, setDenied] = useState(false)
  return denied ? <Alert tone="error">{adminAccessDeniedMessage}</Alert> : <AdminRolesWorkspace onDenied={setDenied} />
}

function AdminRolesWorkspace({ onDenied }: { onDenied: (denied: boolean) => void }) {
  const { user } = useAuth()
  const [roles, setRoles] = useState<AdminRole[]>([])
  const [catalog, setCatalog] = useState<PermissionCatalogItem[]>([])
  const [error, setError] = useState('')
  const [initialized, setInitialized] = useState(false)
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [draft, setDraft] = useState<{ name: string; description: string; permissions: Set<string> }>(
    { name: '', description: '', permissions: new Set() },
  )
  const [newSlug, setNewSlug] = useState('')

  const rolesResource = useCachedResource(adminResourceKey('roles', user), () => api.adminRoles(), adminResourceOptions)
  const catalogResource = useCachedResource(adminResourceKey('permissions', user), () => api.adminRolePermissionCatalog(), adminResourceOptions)
  const accessFailure = adminAccessFailure(rolesResource.error, catalogResource.error)
  useEffect(() => {
    if (!accessFailure) return
    for (const kind of ['roles', 'permissions']) resourceStore.invalidate(adminResourceKey(kind, user))
    onDenied(true)
  }, [accessFailure, user, onDenied])
  const loading = [rolesResource, catalogResource].some(resource => resource.data === undefined && !resource.error)
  useLayoutEffect(() => { if (!loading) setInitialized(true) }, [loading])
  const loadError = rolesResource.error || catalogResource.error
  useEffect(() => { if (rolesResource.data) setRoles(rolesResource.data) }, [rolesResource.data])
  useEffect(() => { if (catalogResource.data) setCatalog(catalogResource.data) }, [catalogResource.data])

  const editing = editingId != null ? roles.find((row) => row.id === editingId) : null
  const ownerRole = editing?.slug === 'royal'
  const effectivePermissions = (slug: string | undefined, permissions: Iterable<string>) => new Set(
    slug === 'royal' ? catalog.map(item => item.key) : [...permissions].filter(key => key !== 'users.approve'),
  )
  const effectiveDraft = effectivePermissions(editing?.slug, draft.permissions)
  const availableCatalog = (ownerRole ? catalog : actorPermissionCatalog(catalog, user?.role))
    .filter(item => ownerRole || item.key !== 'users.approve')
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
        ...(ownerRole ? {} : { permissions: [...effectiveDraft] }),
      })
      setRoles((rows) => rows.map((row) => (row.id === updated.id ? updated : row)))
      resourceStore.invalidate(adminResourceKey('roles', user))
      void rolesResource.refresh()
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
        permissions: [...effectiveDraft],
      })
      setRoles((rows) => [...rows, created])
      resourceStore.invalidate(adminResourceKey('roles', user))
      void rolesResource.refresh()
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
      resourceStore.invalidate(adminResourceKey('roles', user))
      void rolesResource.refresh()
      if (editingId === role.id) setEditingId(null)
    } catch (deleteError) {
      setError(mapApiError(deleteError) || 'Не удалось удалить роль')
    } finally {
      setBusy(false)
    }
  }

  if (accessFailure) return <Alert tone="error">{adminAccessDeniedMessage}</Alert>
  if (loading && !initialized) {
    return <Spinner label="Загрузка ролей…" />
  }

  return (
    <div className="admin-roles">
      {Boolean(error || loadError) && <Alert tone="error">{error || mapApiError(loadError)}</Alert>}

      <MasterDetail detailOpen={detailOpen} onBack={() => setDetailOpen(false)} list={
      <Panel hint="Выберите роль для просмотра и изменения доступов." title="Роли"
        actions={<button className="btn btn-secondary" type="button" onClick={() => {
          setEditingId(null); setNewSlug(''); setDraft({ name: '', description: '', permissions: new Set() }); setDetailOpen(true)
        }}>Новая роль</button>}>
        <ul className="admin-role-list">
          {roles.map((role) => (
            <li key={role.id}>
              <button aria-label={`Открыть роль ${role.name}`} aria-pressed={editingId === role.id} className={`rp-management-select${editingId === role.id ? ' is-selected' : ''}`} onClick={() => startEdit(role)} type="button">
                <EntityRow title={role.name} meta={`${role.slug} · ${role.user_count} польз. · ${effectivePermissions(role.slug, role.permissions).size} разрешений`}
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

        <p className="panel-hint">{ownerRole
          ? 'Владелец всегда имеет все доступы. Этот набор нельзя изменить через настройки роли.'
          : 'Одобрение регистраций доступно только владельцу и не выдаётся другим ролям.'}</p>
        <h4 className="admin-perm-group-title">Разделы меню</h4>
        <div className="admin-perm-grid">
          {navPerms.map((perm) => (
            <label className="admin-perm-check" key={perm.key}>
              <input
                checked={effectiveDraft.has(perm.key)}
                disabled={ownerRole}
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
                checked={effectiveDraft.has(perm.key)}
                disabled={ownerRole}
                onChange={() => togglePerm(perm.key)}
                type="checkbox"
              />
              {perm.label}
            </label>
          ))}
        </div>

        <EffectivePermissions permissions={effectiveDraft} catalog={catalog} />
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
