import { useEffect, useMemo, useState } from 'react'
import { api, type AdminRole, type AdminUser, type Park, type PermissionCatalogItem } from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, Panel } from '../PageShell'
import { Spinner } from '../ui/Feedback'
import { PasswordField } from '../ui/PasswordField'
import { mapApiError } from '../../i18n/errors'
import { accessStatusLabel, roleLabel } from '../../i18n/ru'
import { actorPermissionCatalog, assignableRoles } from './privilegedPermissions'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { EntityRow } from '../../design-system/data/EntityRow'
import { MetricCard } from '../../design-system/data/MetricCard'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { EffectivePermissions } from './EffectivePermissions'

type UserDraft = {
  role_slug: string
  access_status: string
  is_active: boolean
  must_change_password: boolean
  tracker_login: string
  password: string
  park_ids: number[]
  permissions: Set<string>
}

type RoleOption = { slug: string; name: string }

const FALLBACK_ROLES: RoleOption[] = [
  { slug: 'operator', name: 'Оператор' },
  { slug: 'mechanic', name: 'Механик' },
  { slug: 'driver', name: 'Водитель' },
  { slug: 'admin', name: 'Администратор' },
  { slug: 'royal', name: 'Владелец' },
]

function emptyDraft(): UserDraft {
  return {
    role_slug: 'operator',
    access_status: 'approved',
    is_active: true,
    must_change_password: false,
    tracker_login: '',
    password: '',
    park_ids: [],
    permissions: new Set(),
  }
}

function draftFromUser(user: AdminUser): UserDraft {
  return {
    role_slug: user.role,
    access_status: user.access_status,
    is_active: user.is_active,
    must_change_password: user.must_change_password ?? false,
    tracker_login: user.tracker_login ?? '',
    password: '',
    park_ids: user.parks.map((park) => park.id),
    permissions: new Set(user.permissions ?? []),
  }
}

export function AdminUsersPanel({ parks }: { parks: Park[] }) {
  const { user: actor } = useAuth()
  const isRoyal = actor?.role === 'royal'

  const [users, setUsers] = useState<AdminUser[]>([])
  const [roles, setRoles] = useState<AdminRole[]>([])
  const [catalog, setCatalog] = useState<PermissionCatalogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [draft, setDraft] = useState<UserDraft>(emptyDraft())
  const [filterRole, setFilterRole] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [search, setSearch] = useState('')
  const [createForm, setCreateForm] = useState({
    username: '',
    password: '',
    role_slug: 'mechanic',
    parkIds: [] as number[],
    trackerLogin: '',
  })

  const activeParks = parks.filter((park) => park.is_active)
  const pendingCount = users.filter((row) => row.access_status === 'pending').length
  const roleOptions = useMemo<RoleOption[]>(() => {
    if (roles.length > 0) {
      return assignableRoles(roles, isRoyal ? 'royal' : undefined)
        .map((role) => ({ slug: role.slug, name: role.name }))
    }
    return isRoyal ? FALLBACK_ROLES : []
  }, [roles, isRoyal])

  const load = async () => {
    setLoading(true)
    setError('')
    const failures: string[] = []
    try {
      const userRows = await api.adminUsers()
      setUsers(userRows)
      setSelectedId((current) => {
        if (current && userRows.some((row) => row.id === current)) return current
        const pending = userRows.find((row) => row.access_status === 'pending')
        return pending?.id ?? userRows[0]?.id ?? null
      })
    } catch (loadError) {
      failures.push(mapApiError(loadError) || 'Не удалось загрузить пользователей')
    }
    try {
      const [roleRows, permCatalog] = await Promise.all([
        api.adminRoles(),
        api.adminRolePermissionCatalog(),
      ])
      setRoles(roleRows)
      setCatalog(permCatalog)
    } catch (rolesError) {
      failures.push(mapApiError(rolesError) || 'Не удалось загрузить роли')
    }
    if (failures.length) setError(failures.join(' · '))
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [])

  const filteredUsers = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return users.filter((row) => {
      if (filterRole && row.role !== filterRole) return false
      if (filterStatus && row.access_status !== filterStatus) return false
      if (needle && !row.username.toLowerCase().includes(needle)) return false
      return true
    })
  }, [users, filterRole, filterStatus, search])

  const selectedUser = users.find((row) => row.id === selectedId) ?? null
  const selectedLocked = Boolean(
    selectedUser
    && !isRoyal
    && !roleOptions.some((role) => role.slug === selectedUser.role),
  )

  useEffect(() => {
    if (!selectedUser) {
      setDraft(emptyDraft())
      return
    }
    setDraft(draftFromUser(selectedUser))
  }, [selectedUser])

  const selectUser = (user: AdminUser) => {
    setDetailOpen(true)
    setSelectedId(user.id)
    setDraft(draftFromUser(user))
    setSuccess('')
    setError('')
  }

  const availableCatalog = actorPermissionCatalog(catalog, actor?.role)
  const navPerms = availableCatalog.filter(
    (item) => item.category === 'nav' && item.key !== 'users.approve',
  )
  const actionPerms = availableCatalog.filter(
    (item) => item.category === 'action' && item.key !== 'users.approve',
  )
  const roleDefaultPerms = (slug: string): string[] =>
    roles.find((role) => role.slug === slug)?.permissions ?? []

  const toggleDraftPark = (parkId: number) => {
    setDraft((current) => {
      const next = new Set(current.park_ids)
      if (next.has(parkId)) next.delete(parkId)
      else next.add(parkId)
      return { ...current, park_ids: [...next] }
    })
  }

  const toggleCreatePark = (parkId: number) => {
    setCreateForm((current) => {
      const next = new Set(current.parkIds)
      if (next.has(parkId)) next.delete(parkId)
      else next.add(parkId)
      return { ...current, parkIds: [...next] }
    })
  }

  const togglePerm = (key: string) => {
    setDraft((current) => {
      const next = new Set(current.permissions)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return { ...current, permissions: next }
    })
  }

  const changeRole = (slug: string) => {
    setDraft((current) => ({
      ...current,
      role_slug: slug,
      permissions: new Set(roleDefaultPerms(slug)),
    }))
  }

  const saveUser = async () => {
    if (!selectedUser || selectedLocked) return
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      const payload: Parameters<typeof api.updateAdminUser>[1] = {
        role_slug: draft.role_slug,
        is_active: draft.is_active,
        must_change_password: draft.must_change_password,
        tracker_login: draft.tracker_login.trim() || null,
        park_ids: draft.park_ids,
        permissions: [...draft.permissions],
      }
      if (isRoyal) {
        payload.access_status = draft.access_status
      }
      if (draft.password.trim()) {
        payload.password = draft.password
      }
      const updated = await api.updateAdminUser(selectedUser.id, payload)
      setUsers((rows) => rows.map((row) => (row.id === updated.id ? updated : row)))
      setDraft({ ...draftFromUser(updated), password: '' })
      setSuccess('Изменения сохранены')
    } catch (saveError) {
      setError(mapApiError(saveError) || 'Не удалось сохранить пользователя')
    } finally {
      setBusy(false)
    }
  }

  const refreshSelected = async (userId: number) => {
    const rows = await api.adminUsers()
    setUsers(rows)
    const refreshed = rows.find((row) => row.id === userId)
    if (refreshed) setDraft(draftFromUser(refreshed))
  }

  const approveSelected = async () => {
    if (!selectedUser || !isRoyal) return
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await api.approveAdminUser(selectedUser.id, draft.park_ids)
      await refreshSelected(selectedUser.id)
      setSuccess('Пользователь одобрен')
    } catch (approveError) {
      setError(mapApiError(approveError) || 'Не удалось одобрить')
    } finally {
      setBusy(false)
    }
  }

  const rejectSelected = async () => {
    if (!selectedUser || !isRoyal) return
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await api.rejectAdminUser(selectedUser.id)
      await refreshSelected(selectedUser.id)
      setSuccess('Регистрация отклонена')
    } catch (rejectError) {
      setError(mapApiError(rejectError) || 'Не удалось отклонить')
    } finally {
      setBusy(false)
    }
  }

  const createUser = async () => {
    if (!roleOptions.some((role) => role.slug === createForm.role_slug)) return
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      const created = await api.createAdminUser({
        username: createForm.username.trim(),
        password: createForm.password,
        role_slug: createForm.role_slug,
        park_ids: createForm.parkIds,
        tracker_login: createForm.trackerLogin.trim() || null,
      })
      setCreateForm({
        username: '',
        password: '',
        role_slug: 'mechanic',
        parkIds: [],
        trackerLogin: '',
      })
      setUsers((rows) => [...rows, created])
      setSelectedId(created.id)
      setDetailOpen(true)
      setDraft(draftFromUser(created))
      setSuccess('Пользователь создан')
    } catch (createError) {
      setError(mapApiError(createError) || 'Не удалось создать пользователя')
    } finally {
      setBusy(false)
    }
  }

  const deleteSelected = async () => {
    if (!selectedUser || selectedLocked) return
    if (selectedUser.id === actor?.id) {
      setError('Нельзя удалить собственный аккаунт.')
      return
    }
    if (!window.confirm(`Удалить аккаунт «${selectedUser.username}»? Это нельзя отменить.`)) {
      return
    }
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await api.deleteAdminUser(selectedUser.id)
      const remaining = users.filter((row) => row.id !== selectedUser.id)
      setUsers(remaining)
      setSelectedId(remaining[0]?.id ?? null)
      setSuccess('Аккаунт удалён')
    } catch (deleteError) {
      setError(mapApiError(deleteError) || 'Не удалось удалить пользователя')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Spinner label="Загрузка пользователей…" />

  return (
    <div className="admin-users">
      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="rp-management-metrics">
        <MetricCard label="Аккаунты" value={users.length} />
        <MetricCard label="Ожидают одобрения" value={pendingCount} tone={pendingCount ? 'warning' : 'neutral'} />
      </div>
      <MasterDetail detailOpen={detailOpen} onBack={() => setDetailOpen(false)} list={
        <Panel hint="Выберите аккаунт, чтобы сменить роль, парки, пароль и статус." title="Аккаунты">
          <div className="admin-user-filters form-grid">
            <label className="field">
              <span className="field-label">Поиск</span>
              <input
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Логин"
                value={search}
              />
            </label>
            <label className="field">
              <span className="field-label">Роль</span>
              <select onChange={(event) => setFilterRole(event.target.value)} value={filterRole}>
                <option value="">Все</option>
                {roleOptions.map((role) => (
                  <option key={role.slug} value={role.slug}>
                    {role.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Доступ</span>
              <select
                onChange={(event) => setFilterStatus(event.target.value)}
                value={filterStatus}
              >
                <option value="">Все</option>
                <option value="pending">{accessStatusLabel('pending')}</option>
                <option value="approved">{accessStatusLabel('approved')}</option>
                <option value="rejected">{accessStatusLabel('rejected')}</option>
              </select>
            </label>
          </div>
          {pendingCount > 0 && (
            <div className="chip-row admin-user-pending-chip">
              <button
                className={`chip${filterStatus === 'pending' ? ' is-selected' : ''}`}
                onClick={() => setFilterStatus((current) => (current === 'pending' ? '' : 'pending'))}
                type="button"
              >
                Ожидают одобрения · {pendingCount}
              </button>
            </div>
          )}

          <ul className="admin-user-list">
            {filteredUsers.map((row) => (
              <li key={row.id}>
                <button
                  aria-label={`Открыть аккаунт ${row.username}`}
                  aria-pressed={selectedId === row.id}
                  className={`rp-management-select${selectedId === row.id ? ' is-selected' : ''}`}
                  onClick={() => selectUser(row)}
                  type="button"
                >
                  <EntityRow title={row.username}
                    meta={`${roleLabel(row.role)} · ${!row.is_active ? 'выключен' : row.parks.map(park => park.name).join(', ') || 'без парка'}`}
                    status={<StatusBadge tone={row.access_status === 'approved' ? 'success' : row.access_status === 'pending' ? 'warning' : 'critical'}>{accessStatusLabel(row.access_status)}</StatusBadge>} />
                </button>
              </li>
            ))}
            {filteredUsers.length === 0 && (
              <li className="issue-muted">Нет пользователей по фильтру</li>
            )}
          </ul>
        </Panel>
      } detail={
        <Panel
          hint={
            selectedUser
              ? selectedLocked
                ? 'Аккаунт с привилегированной ролью может менять только владелец.'
                : 'Смена пароля или блокировка сбрасывает активные сессии.'
              : 'Выберите пользователя слева или создайте нового ниже.'
          }
          title={selectedUser ? selectedUser.username : 'Карточка пользователя'}
        >
          {selectedUser ? (
            <div className="form-grid">
              <label className="field">
                <span className="field-label">Роль</span>
                <select
                  disabled={selectedLocked || busy}
                  onChange={(event) => changeRole(event.target.value)}
                  value={draft.role_slug}
                >
                  {roleOptions.map((role) => (
                    <option key={role.slug} value={role.slug}>
                      {role.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span className="field-label">Статус доступа</span>
                {isRoyal ? (
                  <select
                    disabled={selectedLocked || busy}
                    onChange={(event) =>
                      setDraft((current) => ({ ...current, access_status: event.target.value }))
                    }
                    value={draft.access_status}
                  >
                    <option value="pending">{accessStatusLabel('pending')}</option>
                    <option value="approved">{accessStatusLabel('approved')}</option>
                    <option value="rejected">{accessStatusLabel('rejected')}</option>
                  </select>
                ) : (
                  <span className="topbar-pill">{accessStatusLabel(draft.access_status)}</span>
                )}
              </label>

              <label className="field admin-perm-check">
                <input
                  checked={draft.is_active}
                  disabled={selectedLocked || busy}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, is_active: event.target.checked }))
                  }
                  type="checkbox"
                />
                Аккаунт активен
              </label>

              <label className="field admin-perm-check">
                <input
                  checked={draft.must_change_password}
                  disabled={selectedLocked || busy}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      must_change_password: event.target.checked,
                    }))
                  }
                  type="checkbox"
                />
                Требовать смену пароля при входе
              </label>

              <label className="field">
                <span className="field-label">Tracker login</span>
                <input
                  disabled={selectedLocked || busy}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, tracker_login: event.target.value }))
                  }
                  placeholder="mechanic.dev"
                  value={draft.tracker_login}
                />
              </label>

              <PasswordField
                disabled={selectedLocked || busy}
                label="Новый пароль"
                onChange={(value) =>
                  setDraft((current) => ({ ...current, password: value }))
                }
                placeholder="Оставьте пустым, чтобы не менять"
                value={draft.password}
              />

              <div className="field">
                <span className="field-label">Парки</span>
                {parks.length === 0 ? (
                  <p className="issue-muted">Сначала создайте парк во вкладке «Парки».</p>
                ) : (
                  <div className="admin-park-checks">
                    {parks.map((park) => {
                      const assigned = draft.park_ids.includes(park.id)
                      const lockedInactive = !park.is_active && !assigned
                      return (
                        <label className="admin-perm-check" key={park.id}>
                          <input
                            checked={assigned}
                            disabled={selectedLocked || busy || lockedInactive}
                            onChange={() => toggleDraftPark(park.id)}
                            type="checkbox"
                          />
                          {park.name}
                          {!park.is_active ? (
                            <span className="issue-muted"> · неактивен</span>
                          ) : null}
                        </label>
                      )
                    })}
                  </div>
                )}
              </div>

              {draft.role_slug === 'royal' ? (
                <p className="issue-muted">Владелец всегда имеет все доступы. Одобрение регистраций — только у этой роли.</p>
              ) : (
                <div className="field rp-permissions-editor">
                  <span className="field-label">Доступы этого человека</span>
                  <p className="field-hint">
                    Роль задаёт базовый набор. Снимите галочку, чтобы забрать доступ, или поставьте — чтобы выдать сверх роли.
                  </p>
                  <h4 className="admin-perm-group-title">Разделы меню</h4>
                  <div className="admin-perm-grid">
                    {navPerms.map((perm) => (
                      <label className="admin-perm-check" key={perm.key}>
                        <input
                          checked={draft.permissions.has(perm.key)}
                          disabled={selectedLocked || busy}
                          onChange={() => togglePerm(perm.key)}
                          type="checkbox"
                        />
                        {perm.label}
                        {roleDefaultPerms(draft.role_slug).includes(perm.key) ? (
                          <span className="issue-muted"> · роль</span>
                        ) : null}
                      </label>
                    ))}
                  </div>
                  <h4 className="admin-perm-group-title">Действия</h4>
                  <div className="admin-perm-grid">
                    {actionPerms.map((perm) => (
                      <label className="admin-perm-check" key={perm.key}>
                        <input
                          checked={draft.permissions.has(perm.key)}
                          disabled={selectedLocked || busy}
                          onChange={() => togglePerm(perm.key)}
                          type="checkbox"
                        />
                        {perm.label}
                        {roleDefaultPerms(draft.role_slug).includes(perm.key) ? (
                          <span className="issue-muted"> · роль</span>
                        ) : null}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <EffectivePermissions catalog={catalog} permissions={draft.role_slug === 'royal' ? new Set(catalog.map(item => item.key)) : draft.permissions} />

              <div className="form-actions">
                {!selectedLocked && (
                  <button className="btn" disabled={busy} onClick={() => void saveUser()} type="button">
                    {busy ? <Spinner label="Сохранение" /> : 'Сохранить'}
                  </button>
                )}
                {isRoyal && selectedUser.access_status === 'pending' && (
                  <>
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => void approveSelected()}
                      type="button"
                    >
                      Одобрить
                    </button>
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => void rejectSelected()}
                      type="button"
                    >
                      Отклонить
                    </button>
                  </>
                )}
              </div>
                {!selectedLocked && selectedUser.id !== actor?.id && (
                  <section aria-label="Опасные действия" className="rp-danger-zone">
                  <h3>Опасные действия</h3>
                  <p>Удаление аккаунта нельзя отменить.</p>
                  <button
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() => void deleteSelected()}
                    type="button"
                  >
                    Удалить аккаунт
                  </button>
                  </section>
                )}
            </div>
          ) : (
            <p className="issue-muted">Выберите пользователя в списке слева.</p>
          )}
        </Panel>
      } />

      <Panel title="Создать пользователя">
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Логин</span>
            <input
              onChange={(event) =>
                setCreateForm((current) => ({ ...current, username: event.target.value }))
              }
              value={createForm.username}
            />
          </label>
          <PasswordField
            label="Пароль"
            onChange={(value) =>
              setCreateForm((current) => ({ ...current, password: value }))
            }
            value={createForm.password}
          />
          <label className="field">
            <span className="field-label">Роль</span>
            <select
              onChange={(event) =>
                setCreateForm((current) => ({ ...current, role_slug: event.target.value }))
              }
              value={createForm.role_slug}
            >
              {roleOptions
                .filter((role) => role.slug !== 'royal')
                .map((role) => (
                  <option key={role.slug} value={role.slug}>
                    {role.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Tracker login</span>
            <input
              onChange={(event) =>
                setCreateForm((current) => ({ ...current, trackerLogin: event.target.value }))
              }
              value={createForm.trackerLogin}
            />
          </label>
          <div className="field">
            <span className="field-label">Парки</span>
            <div className="admin-park-checks">
              {activeParks.map((park) => (
                <label className="admin-perm-check" key={park.id}>
                  <input
                    checked={createForm.parkIds.includes(park.id)}
                    onChange={() => toggleCreatePark(park.id)}
                    type="checkbox"
                  />
                  {park.name}
                </label>
              ))}
            </div>
          </div>
        </div>
        <div className="form-actions">
          <button
            className="btn"
            disabled={busy || !createForm.username.trim() || !createForm.password || !roleOptions.some((role) => role.slug === createForm.role_slug)}
            onClick={() => void createUser()}
            type="button"
          >
            Создать
          </button>
        </div>
      </Panel>
    </div>
  )
}
