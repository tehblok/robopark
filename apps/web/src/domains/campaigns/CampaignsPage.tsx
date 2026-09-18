import { type CSSProperties, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  type Campaign,
  type CampaignCreatePayload,
  type CampaignDetail,
  type CampaignTicket,
} from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { ParkMultiSelect } from '../../components/admin/ParkMultiSelect'
import { Button } from '../../design-system/actions/Button'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from '../../design-system/layout/ResponsiveDisclosure'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { refreshReportsBadge } from '../../reports-badge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import './campaigns.css'

type CampaignApi = Pick<typeof api, 'campaigns' | 'campaign' | 'refreshCampaign' | 'deleteCampaign' | 'createCampaign' | 'updateCampaign' | 'completeCampaignTicket'>

const kindLabel = (kind: Campaign['kind']) => kind === 'service_company' ? 'Сервисная компания' : 'Оклейка'
const campaignStatusLabel = (campaign: Campaign) => !campaign.is_active ? 'Завершена' : campaign.overdue ? 'Просрочена' : 'Активна'
const dateLabel = (value: string) => new Intl.DateTimeFormat('ru-RU').format(new Date(`${value}T00:00:00`))

function Progress({ value, label }: { value: number; label: string }) {
  const safe = Math.max(0, Math.min(100, value))
  return <div aria-label={`${label}: ${safe}%`} className="campaign-progress" role="img" style={{ '--campaign-progress': `${safe * 3.6}deg` } as CSSProperties}>
    <strong>{safe}%</strong><span>выполнено</span>
  </div>
}

export function CampaignMetrics({ campaign }: { campaign: Campaign }) {
  return <div className="campaign-metrics">
    <Progress label={campaign.name} value={campaign.percent_complete} />
    <div className="stat-grid">
      <MetricCard label="Всего тикетов" value={campaign.total_count} />
      <MetricCard label="Выполнено" tone="success" value={campaign.completed_count} />
      <MetricCard label="На проверке" tone="info" value={campaign.pending_review_count} />
      <MetricCard label="Осталось" tone={campaign.overdue ? 'critical' : 'neutral'} value={campaign.remaining_count} />
    </div>
  </div>
}

export function CampaignOverviewSection({ parkId, apiClient = api }: { parkId: number; apiClient?: CampaignApi }) {
  const [items, setItems] = useState<Campaign[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let active = true
    apiClient.campaigns(parkId).then((value) => { if (active) { setItems(value.filter(item => item.is_active)); setError(null) } })
      .catch((reason) => { if (active) setError(reason) })
    return () => { active = false }
  }, [apiClient, parkId])
  if (error) return null
  if (!items?.length) return null
  return <Panel collapsible storageKey={`overview-campaigns-${parkId}`} title="СК и оклейка">
    <div className="campaign-overview-list">{items.map(item => <article className="campaign-overview-item" key={item.id}>
      <div><StatusBadge tone={item.overdue ? 'critical' : 'info'}>{kindLabel(item.kind)}</StatusBadge><h3><Link to={`/campaigns/${item.id}`}>{item.name}</Link></h3><p>{item.park_names.join(', ')} · до {dateLabel(item.due_on)}</p></div>
      <Progress label={item.name} value={item.percent_complete} />
    </article>)}</div>
  </Panel>
}

function CampaignCreateForm({ apiClient, onCreated }: { apiClient: CampaignApi; onCreated: (item: Campaign) => void }) {
  const { parks } = useParkScope()
  const today = new Date().toISOString().slice(0, 10)
  const [payload, setPayload] = useState<CampaignCreatePayload>({ kind: 'service_company', name: '', tracker_tag: '', starts_on: today, due_on: today, park_ids: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true); setError(null)
    try { onCreated(await apiClient.createCampaign(payload)) }
    catch (reason) { setError(classifyApiError(reason, 'Не удалось создать кампанию.').description) }
    finally { setBusy(false) }
  }
  return <ResponsiveDisclosureGroup label="Создание кампании"><ResponsiveDisclosure id="create" title="Новая кампания">
    <form className="form-grid campaign-create" onSubmit={submit}>
      <label className="field"><span>Тип</span><select value={payload.kind} onChange={event => setPayload(current => ({ ...current, kind: event.target.value as Campaign['kind'] }))}><option value="service_company">Сервисная компания</option><option value="wrapping">Оклейка</option></select></label>
      <label className="field"><span>Название</span><input required maxLength={128} value={payload.name} onChange={event => setPayload(current => ({ ...current, name: event.target.value }))} /></label>
      <label className="field"><span>Часть названия тикета</span><input required minLength={3} maxLength={128} value={payload.tracker_tag} onChange={event => setPayload(current => ({ ...current, tracker_tag: event.target.value }))} /></label>
      <label className="field"><span>Начало</span><input required type="date" value={payload.starts_on} onChange={event => setPayload(current => ({ ...current, starts_on: event.target.value }))} /></label>
      <label className="field"><span>Срок</span><input required min={payload.starts_on} type="date" value={payload.due_on} onChange={event => setPayload(current => ({ ...current, due_on: event.target.value }))} /></label>
      <ParkMultiSelect label="Парки кампании" onChange={park_ids => setPayload(current => ({ ...current, park_ids }))} parks={parks} value={payload.park_ids} />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <Button busy={busy} disabled={!payload.park_ids.length} type="submit">Создать кампанию</Button>
    </form>
  </ResponsiveDisclosure></ResponsiveDisclosureGroup>
}

function CampaignList({ apiClient }: { apiClient: CampaignApi }) {
  const { user } = useAuth()
  const { selectedPark } = useParkScope()
  const navigate = useNavigate()
  const [items, setItems] = useState<Campaign[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => {
    setError(null)
    apiClient.campaigns(selectedPark?.id).then(setItems).catch(setError)
  }, [apiClient, selectedPark?.id])
  useEffect(load, [load])
  const manager = user?.role === 'admin' || user?.role === 'royal'
  const failure = error ? classifyApiError(error, 'Не удалось загрузить кампании.') : null
  return <PageLayout description="Прогресс сервисных компаний и оклейки по доступным паркам." title="СК и оклейка">
    {manager ? <CampaignCreateForm apiClient={apiClient} onCreated={item => navigate(`/campaigns/${item.id}`)} /> : null}
    {failure ? <ErrorState description={failure.description} onRetry={failure.retryable ? load : undefined} title={failure.title} />
      : !items ? <LoadingState label="Загружаем кампании" variant="page" />
        : !items.length ? <EmptyState description="Администратор ещё не добавил кампании для доступных парков." icon="work" title="Кампаний нет" />
          : <div className="campaign-list">{items.map(item => <Panel className="campaign-card" density="dense" key={item.id}>
            <div className="campaign-card__heading"><div><div className="campaign-card__badges"><StatusBadge tone="neutral">{kindLabel(item.kind)}</StatusBadge><StatusBadge tone={!item.is_active ? 'neutral' : item.overdue ? 'critical' : 'info'}>{campaignStatusLabel(item)}</StatusBadge></div><h2><Link to={`/campaigns/${item.id}`}>{item.name}</Link></h2><p>{item.park_names.join(', ')} · {dateLabel(item.starts_on)} — {dateLabel(item.due_on)}</p></div><Progress label={item.name} value={item.percent_complete} /></div>
            <ResponsiveDisclosureGroup label={`Метрики ${item.name}`}><ResponsiveDisclosure id="metrics" summary={`${item.percent_complete}% выполнено`} title="Метрики"><CampaignMetrics campaign={item} /></ResponsiveDisclosure></ResponsiveDisclosureGroup>
          </Panel>)}</div>}
  </PageLayout>
}

function transitionLabel(value: string | null) {
  if (value === 'pending') return 'Отправка в Tracker ожидается'
  if (value === 'review') return 'Tracker: Проверка'
  if (value === 'diagnostics') return 'Tracker: Диагностика'
  if (value === 'failed') return 'Не удалось сменить статус в Tracker'
  if (value === 'unavailable') return 'Нет перехода в Проверку или Диагностику'
  if (value === 'cancelled') return 'Отправка отменена после возврата результата'
  return null
}

function TicketCard({ ticket, campaign, apiClient, reload, editing, onEditingChange }: { ticket: CampaignTicket; campaign: CampaignDetail; apiClient: CampaignApi; reload: () => void; editing: boolean; onEditingChange: (editing: boolean) => void }) {
  const [comment, setComment] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submitAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!photo) return
    setBusy(true); setError(null)
    try {
      const fingerprint = `${ticket.key}:${ticket.park_id}:${comment}:${photo.name}:${photo.size}:${photo.lastModified}`
      if (submitAttempt.current?.fingerprint !== fingerprint) {
        submitAttempt.current = { fingerprint, key: globalThis.crypto?.randomUUID?.() ?? `campaign-${Date.now()}-${Math.random()}` }
      }
      await apiClient.completeCampaignTicket(campaign.id, ticket.key, ticket.park_id, comment, photo, submitAttempt.current.key)
      submitAttempt.current = null
      refreshReportsBadge(); reload()
    } catch (reason) { setError(classifyApiError(reason, 'Не удалось отправить тикет оператору.').description) }
    finally { setBusy(false) }
  }
  const trackerState = transitionLabel(ticket.tracker_transition)
  const canSubmit = ticket.review_status == null || ticket.review_status === 'returned'
  return <article className="campaign-ticket">
    <div className="campaign-ticket__head"><div><a href={ticket.url} rel="noreferrer" target="_blank"><strong>{ticket.key}</strong></a><h3>{ticket.robot || 'Робот не указан'}</h3></div><StatusBadge tone={ticket.review_status === 'done' || ticket.review_status === 'tracker_closed' ? 'success' : ticket.review_status === 'returned' ? 'warning' : 'neutral'}>{ticket.status}</StatusBadge></div>
    <p>{ticket.summary}</p><small>{ticket.park_name}</small>
    {ticket.comment ? <blockquote>{ticket.comment}</blockquote> : null}
    {trackerState ? <p className={ticket.tracker_transition === 'failed' || ticket.tracker_transition === 'unavailable' ? 'campaign-warning' : ''}>{trackerState}</p> : null}
    {canSubmit ? <>{editing ? <form className="campaign-complete" onSubmit={submit}>
      <label className="field"><span>Комментарий для оператора</span><textarea required maxLength={4000} value={comment} onChange={event => setComment(event.target.value)} /></label>
      <label className="field"><span>Фото</span><input accept="image/jpeg,image/png,image/webp" required type="file" onChange={event => setPhoto(event.target.files?.[0] ?? null)} /></label>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="campaign-actions"><Button busy={busy} disabled={!photo || !comment.trim()} type="submit">Отправить оператору</Button><Button onClick={() => onEditingChange(false)} type="button" variant="ghost">Отмена</Button></div>
    </form> : <Button onClick={() => onEditingChange(true)} variant="secondary">Заполнить и отправить на проверку</Button>}</> : null}
    {ticket.review_status === 'open' && ticket.report_id ? <Link to={`/reports/${ticket.report_id}`}>Открыть проверку оператора</Link> : null}
  </article>
}

function CampaignDetailPage({ campaignId, apiClient }: { campaignId: number; apiClient: CampaignApi }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState<CampaignDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [editingTicketKey, setEditingTicketKey] = useState<string | null>(null)
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null)
  const load = useCallback(() => { setError(null); apiClient.campaign(campaignId).then(setData).catch(setError) }, [apiClient, campaignId])
  useEffect(load, [load])
  useEffect(() => {
    if (data?.snapshot_state !== 'pending' && data?.snapshot_state !== 'running') return
    const timer = window.setInterval(load, 3000)
    return () => window.clearInterval(timer)
  }, [data?.snapshot_state, load])
  const refresh = async () => {
    setRefreshMessage(null)
    try {
      await apiClient.refreshCampaign(campaignId)
      setRefreshMessage('Обновление запрошено. Пока показаны последние сохранённые данные.')
      load()
    } catch (reason) {
      setRefreshMessage(classifyApiError(reason, 'Не удалось запросить обновление.').description)
    }
  }
  const remove = async () => {
    if (!window.confirm('Удалить кампанию? Если проверки уже отправлены, репорты и результаты сохранятся в архиве.')) return
    try {
      await apiClient.deleteCampaign(campaignId)
      navigate('/campaigns')
    } catch (reason) {
      setRefreshMessage(classifyApiError(reason, 'Не удалось удалить кампанию.').description)
    }
  }
  const open = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('ru')
    return data?.open_tickets.filter(ticket => !needle || `${ticket.robot || ''} ${ticket.key} ${ticket.summary}`.toLocaleLowerCase('ru').includes(needle)) ?? []
  }, [data, query])
  const manager = user?.role === 'admin' || user?.role === 'royal'
  const failure = error ? classifyApiError(error, 'Не удалось загрузить кампанию.') : null
  if (failure && !data) return <PageLayout title="СК и оклейка"><ErrorState description={failure.description} onRetry={failure.retryable ? load : undefined} title={failure.title} /></PageLayout>
  if (!data) return <PageLayout title="СК и оклейка"><LoadingState label="Загружаем кампанию" variant="page" /></PageLayout>
  return <PageLayout actions={<><Button onClick={() => void refresh()} variant="secondary">Обновить из Tracker</Button>{manager ? <><Button onClick={() => void apiClient.updateCampaign(data.id, { is_active: !data.is_active }).then(load)} variant="secondary">{data.is_active ? 'Завершить кампанию' : 'Возобновить кампанию'}</Button><Button onClick={() => void remove()} variant="ghost">Удалить кампанию</Button></> : null}</>} description={`${data.park_names.join(', ')} · ${data.tracker_tag} · ${dateLabel(data.starts_on)} — ${dateLabel(data.due_on)}`} eyebrow={<Link to="/campaigns">СК и оклейка</Link>} title={data.name}>
    <p role="status">{data.snapshot_at ? `Последнее обновление: ${new Date(data.snapshot_at).toLocaleString('ru-RU')}. ` : 'Данные Tracker ещё не получены. '}{data.snapshot_state === 'error' ? 'Tracker временно недоступен; показаны сохранённые данные.' : data.snapshot_state === 'pending' || data.snapshot_state === 'running' ? 'Обновляем в фоне.' : null}</p>
    {refreshMessage ? <p role="status">{refreshMessage}</p> : null}
    {failure ? <p role="status">Показаны последние полученные данные. Обновление не удалось: {failure.description}</p> : null}
    <ResponsiveDisclosureGroup label="Разделы кампании"><ResponsiveDisclosure id="metrics" summary={`${data.percent_complete}% · ${data.completed_count} из ${data.total_count}`} title="Метрики"><CampaignMetrics campaign={data} /></ResponsiveDisclosure></ResponsiveDisclosureGroup>
    <div className="campaign-columns">
      <Panel density="dense" title={`Открытые · ${data.open_tickets.length}`}><label className="field campaign-search"><span>Поиск по роботу</span><input onChange={event => setQuery(event.target.value)} placeholder="Номер робота или тикет" value={query} /></label>
        <div className="campaign-tickets">{open.map(ticket => <TicketCard apiClient={apiClient} campaign={data} editing={editingTicketKey === ticket.key} key={ticket.key} onEditingChange={editing => setEditingTicketKey(editing ? ticket.key : null)} reload={load} ticket={ticket} />)}{!open.length ? <p>Открытые тикеты не найдены.</p> : null}</div>
      </Panel>
      <Panel density="dense"><ResponsiveDisclosureGroup label="История кампании"><ResponsiveDisclosure id="closed" summary={`${data.closed_tickets.length} тикетов`} title={`Закрытые · ${data.closed_tickets.length}`}><div className="campaign-tickets">{data.closed_tickets.map(ticket => <TicketCard apiClient={apiClient} campaign={data} editing={editingTicketKey === ticket.key} key={ticket.key} onEditingChange={editing => setEditingTicketKey(editing ? ticket.key : null)} reload={load} ticket={ticket} />)}{!data.closed_tickets.length ? <p>Закрытых тикетов пока нет.</p> : null}</div></ResponsiveDisclosure></ResponsiveDisclosureGroup></Panel>
    </div>
  </PageLayout>
}

export function CampaignsPage({ apiClient = api }: { apiClient?: CampaignApi }) {
  const raw = useParams().campaignId
  return raw && /^\d+$/.test(raw) ? <CampaignDetailPage apiClient={apiClient} campaignId={Number(raw)} /> : <CampaignList apiClient={apiClient} />
}
