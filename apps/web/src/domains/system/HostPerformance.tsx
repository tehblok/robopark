import { Alert, Panel } from '../../components/PageShell'
import { MetricCard } from '../../design-system/data/MetricCard'

type RecordValue = Record<string, unknown>

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {}
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function decimal(value: number, digits = 1): string {
  return value.toLocaleString('ru-RU', { maximumFractionDigits: digits }).replace(/\u00a0/g, ' ')
}

function unavailable(state: unknown): string | null {
  if (state === 'stale') return 'Измерение производительности устарело. Проверьте watchdog хоста.'
  if (state === 'invalid') return 'Снимок производительности повреждён и скрыт.'
  if (state === 'missing') return 'Измерение производительности ещё не получено.'
  return null
}

export function HostPerformance({ value }: { value: unknown }) {
  const performance = record(value)
  const warning = unavailable(performance.source_state)
  if (performance.source_state !== 'reported') {
    return <Panel density="dense" title="Производительность хоста">
      <Alert tone="warning">{warning ?? 'Состояние измерений неизвестно.'}</Alert>
    </Panel>
  }
  const cpu = record(performance.cpu)
  const frequency = record(cpu.frequency_mhz)
  const pressure = record(performance.pressure)
  const memoryPressure = record(pressure.memory)
  const ioPressure = record(pressure.io)
  const temperatures = record(performance.temperatures_c)
  const gpu = record(performance.gpu)
  const wifi = record(performance.wifi)
  const sampledAt = number(performance.sampled_at)
  const averageFrequency = number(frequency.average)
  const onlineCores = number(cpu.online_cores)
  const cpuTemperature = number(temperatures.cpu)
  const gpuTemperature = number(temperatures.gpu)
  const socTemperature = number(temperatures.soc)
  const nvmeTemperature = number(temperatures.nvme)
  const gpuLoad = number(gpu.load_percent)
  const signal = number(wifi.signal_dbm)
  const memorySome = number(memoryPressure.some_avg10)
  const ioSome = number(ioPressure.some_avg10)
  const rxErrors = number(wifi.rx_errors)
  const txErrors = number(wifi.tx_errors)
  const wifiErrors = `Ошибки: RX ${rxErrors === null ? 'не измерено' : decimal(rxErrors, 0)} · TX ${txErrors === null ? 'не измерено' : decimal(txErrors, 0)}`
  const otherTemperatures = [
    gpuTemperature === null ? null : `GPU ${decimal(gpuTemperature)} °C`,
    socTemperature === null ? null : `SoC ${decimal(socTemperature)} °C`,
    nvmeTemperature === null ? null : `NVMe ${decimal(nvmeTemperature)} °C`,
  ].filter((item): item is string => item !== null).join(' · ')
  return <Panel density="dense" title="Производительность хоста">
    <div className="rp-system-grid">
      <MetricCard label="CPU" value={onlineCores === null ? 'Не измерено' : `${onlineCores} онлайн`} delta={averageFrequency === null ? undefined : `${decimal(averageFrequency)} МГц`} />
      <MetricCard label="Температура" value={cpuTemperature === null ? 'Не измерено' : `CPU ${decimal(cpuTemperature)} °C`} delta={otherTemperatures || undefined} />
      <MetricCard label="Ожидание памяти и диска · 10 с" value={memorySome === null ? 'Не измерено' : `Память ${decimal(memorySome)}%`} delta={ioSome === null ? undefined : `Диск ${decimal(ioSome)}%`} />
      <MetricCard label="GPU" value={gpuLoad === null ? 'Не измерено' : `${decimal(gpuLoad)}%`} delta={number(gpu.frequency_mhz) === null ? undefined : `${decimal(number(gpu.frequency_mhz) as number)} МГц`} />
      <MetricCard label="Wi‑Fi" value={signal === null ? 'Не измерено' : `−${decimal(Math.abs(signal))} dBm`} delta={wifiErrors} />
    </div>
    <p>{sampledAt === null ? 'Время снимка неизвестно.' : `Снимок: ${new Date(sampledAt * 1000).toLocaleString('ru-RU')}.`} Низкая частота сама по себе не доказывает снижение частоты из-за перегрева.</p>
  </Panel>
}
