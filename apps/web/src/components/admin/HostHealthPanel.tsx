import { useAuth } from '../../auth-context'
import { useCachedResource } from '../../lib/resource'
import { Alert, Panel } from '../PageShell'
import { SkeletonList } from '../ui/Feedback'
import { adminAccessFailure, adminResourceKey } from './adminResources'
import { hostHealthApi } from './hostHealthApi'
import './HostHealthPanel.css'

function bytes(value: number | null) {
  if (value == null) return 'Нет данных'
  return `${(value / 1024 ** 3).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ГБ`
}
function stamp(value: number) { return new Date(value * 1000).toLocaleString('ru-RU') }

export function HostHealthPanel() {
  const { user } = useAuth()
  const allowed = Boolean(user?.permissions?.includes('nav.admin'))
  const resource = useCachedResource(adminResourceKey('host-health', user), hostHealthApi.get, {
    enabled: allowed, persist: false, refreshIntervalMs: 30_000, staleTimeMs: 30_000,
  })
  if (!allowed || adminAccessFailure(resource.error)) return <Alert tone="error">Доступ к состоянию сервера закрыт.</Alert>
  const data = resource.data
  if (!data) return resource.error ? <Alert tone="error">Не удалось получить состояние сервера. Повторная проверка произойдёт автоматически.</Alert> : <SkeletonList rows={3} />
  const cleanupFailed = data.storage?.cleanup_failed === true || data.storage?.api_cleanup_failed === true
  const cleanupBusy = data.storage?.cleanup_busy === true && !cleanupFailed
  const diskLow = data.disk.free_bytes != null && (data.disk.free_bytes < 2 * 1024 ** 3 || (data.disk.total_bytes != null && data.disk.free_bytes / data.disk.total_bytes < 0.1))
  const memoryLow = data.memory.available_bytes != null && data.memory.total_bytes != null && data.memory.available_bytes / data.memory.total_bytes < 0.1
  const containerHigh = data.memory.container_limit_bytes != null && data.memory.container_used_bytes != null && data.memory.container_used_bytes / data.memory.container_limit_bytes > 0.9
  const requests = Object.entries(data.requests)
  return <div className="stack rp-host-health">
    <p className="muted">Состояние на {stamp(data.sampled_at)}. Данные обновляются автоматически.</p>
    {Boolean(resource.error) && <Alert tone="error">Сейчас нет свежего ответа. Показано последнее полученное состояние.</Alert>}
    {data.disk.source_state === 'unavailable' && <Alert tone="error">Не удалось измерить диск API. Проверьте доступность каталога данных на хосте.</Alert>}
    {data.host_health_source_state === 'unavailable' && <Alert tone="error">Снимок host agent отсутствует или повреждён. Проверьте службу host agent и её журнал.</Alert>}
    {diskLow && <Alert tone="error">Мало свободного места: менее 2 ГБ или 10% диска. Освободите место для данных и резервных копий.</Alert>}
    {(memoryLow || containerHigh) && <Alert tone="error">Мало свободной памяти: доступно менее 10% памяти хоста или занято более 90% лимита API.</Alert>}
    {requests.some(([, metric]) => metric && metric.errors > 0) && <Alert tone="error">Есть ошибки ответов за последние пять минут. Проверьте показатели ниже.</Alert>}
    {requests.some(([, metric]) => metric?.average_ms != null && metric.average_ms > 2000) && <Alert tone="error">Ответы замедлились: среднее время превышает две секунды.</Alert>}
    <Panel density="dense" title="Сервер" hint="Память хоста и лимит контейнера показаны отдельно. На неподдерживаемой платформе значение остаётся неизвестным.">
      <div className="stat-grid">
        <div className="stat"><span className="stat-label">PostgreSQL</span><span className="stat-value">Отвечает</span></div>
        <div className="stat"><span className="stat-label">Свободно на диске данных</span><span className="stat-value">{bytes(data.disk.free_bytes)}</span></div>
        <div className="stat"><span className="stat-label">Доступно памяти хоста</span><span className="stat-value">{bytes(data.memory.available_bytes)}</span></div>
        <div className="stat"><span className="stat-label">Всего памяти хоста</span><span className="stat-value">{bytes(data.memory.total_bytes)}</span></div>
        <div className="stat"><span className="stat-label">Память контейнера API</span><span className="stat-value">{bytes(data.memory.container_used_bytes)} / {bytes(data.memory.container_limit_bytes)}</span></div>
      </div>
    </Panel>
    {data.storage && data.process && <Panel density="dense" title="Хранение и процесс" hint="Порог свободного места — большее из 15% раздела и 6 ГБ. Первичные данные в автоочистку не входят.">
      <div className="stat-grid">
        <div className="stat"><span className="stat-label">Порог свободного места</span><span className="stat-value">{bytes(data.storage.floor_bytes)}</span></div>
        <div className="stat"><span className="stat-label">Нужно освободить</span><span className="stat-value">{bytes(data.storage.bytes_to_reclaim)}</span></div>
        <div className="stat"><span className="stat-label">Последняя уборка</span><span className="stat-value">{data.storage.last_cleanup_at == null ? 'Нет данных' : stamp(data.storage.last_cleanup_at)}</span></div>
        <div className="stat"><span className="stat-label">RSS процесса</span><span className="stat-value">{bytes(data.process.rss_bytes)}</span></div>
        <div className="stat"><span className="stat-label">Дескрипторы / tasks / threads</span><span className="stat-value">{data.process.open_fds ?? '—'} / {data.process.tasks} / {data.process.threads}</span></div>
        <div className="stat"><span className="stat-label">Кэш / соединения БД</span><span className="stat-value">{bytes(data.process.cache_bytes)} / {data.process.db_pool_checked_out ?? '—'}</span></div>
      </div>
      {cleanupFailed && <Alert tone="error">Автоочистка не завершилась. Проверьте журнал host agent.</Alert>}
      {cleanupBusy && <Alert tone={data.storage.space_pressure ? 'error' : 'warning'}>{data.storage.space_pressure && 'Свободного места недостаточно. '}Автоочистка отложена до завершения текущей операции.</Alert>}
      {data.storage.space_pressure && data.storage.cleanup_failed === false && !cleanupFailed && !cleanupBusy && <Alert tone="error">Автоочистка завершилась, но свободного места всё ещё недостаточно.</Alert>}
      {data.storage.space_pressure && data.storage.cleanup_failed == null && !cleanupBusy && <Alert tone="error">Свободного места недостаточно; результат автоочистки неизвестен.</Alert>}
      {data.process.memory_pressure?.failed && <Alert tone="error">Давление памяти сохраняется после очистки кэша. Перезапуск не выполнялся.</Alert>}
    </Panel>}
    {data.capabilities && <Panel density="dense" title="Аппаратные возможности" hint="Ускорение выбирается только после проверки; при отказе остаётся software JPEG.">
      {data.capabilities.source_state === 'invalid'
        ? <Alert tone="error">Повреждены сведения о возможностях хоста. Проверьте host bridge и его журнал; аппаратное ускорение не подтверждено.</Alert>
        : data.capabilities.source_state === 'stale'
        ? <Alert tone="warning">Проверка аппаратных возможностей хоста устарела. Проверьте запуск host doctor; прежний профиль не подтверждён.</Alert>
        : data.capabilities.source_state === 'missing'
        ? <Alert tone="warning">Проверка аппаратных возможностей хоста ещё не получена.</Alert>
        : <><p>Профиль: {data.capabilities.profile} · JPEG: {data.capabilities.jpeg_backend}</p>
          <p>NPU: {data.capabilities.npu_available ? 'доступен' : 'нет'} · CUDA: {data.capabilities.cuda_available ? 'доступна' : 'нет'}</p></>}
    </Panel>}
    <Panel density="dense" title="Резервная копия" hint="Подтверждение появляется после проверки снимка и сохранения копии на хосте командой backup. Ручные снимки показаны в разделе обслуживания.">
      <p>{data.backup.verified_at == null ? 'Проверенная копия ещё не отмечена.' : `Последняя проверенная копия: ${stamp(data.backup.verified_at)}`}</p>
      {data.backup.overdue && <Alert tone="error">Более 36 часов без подтверждённой резервной копии.</Alert>}
      {data.backup.last_attempt_failed && <Alert tone="error">Последняя попытка создать снимок завершилась ошибкой.</Alert>}
    </Panel>
    <Panel density="dense" title="Ответы приложения" hint="Последние пять минут, все API-процессы. Время ответа Robopark включает обработку и кэш; это не отдельный замер внешнего сервиса. Наблюдения могут отставать на 10 секунд.">
      <div className="stat-grid">{(['tracker', 'diagnostics', 'reports'] as const).map(family => {
        const metric = data.requests[family]
        return <div className="stat" key={family}>
          <strong>{{ tracker: 'Tracker', diagnostics: 'Проверка робота', reports: 'Репорты' }[family]}</strong>
          {!metric?.requests ? <p>Нет данных за этот период</p> : <>
            <p>Запросов: {metric.requests} · Ошибок: {metric.errors} · Ограничений 429: {metric.limited}</p>
            <p>Среднее: {metric.average_ms} мс · Максимум: {metric.max_ms} мс</p>
          </>}
        </div>
      })}</div>
    </Panel>
  </div>
}
