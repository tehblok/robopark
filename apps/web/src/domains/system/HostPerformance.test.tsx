import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HostPerformance } from './HostPerformance'

describe('HostPerformance', () => {
  it('shows concise fresh measurements with timestamp and caveat', () => {
    render(<HostPerformance value={{
      source_state: 'reported', sampled_at: 1_799_999_900,
      cpu: { online_cores: 8, frequency_mhz: { min: 800, average: 1200, max: 1600 } },
      pressure: { memory: { some_avg10: 1.5, full_avg10: null }, io: { some_avg10: 2.5, full_avg10: 0.25 } },
      temperatures_c: { cpu: 52.5, gpu: null, soc: 48, nvme: 41 },
      gpu: { frequency_mhz: 612, load_percent: 37.5 },
      wifi: { interface_count: 1, signal_dbm: -43, rx_errors: 2, tx_errors: 3 },
    }} />)

    expect(screen.getByText('8 онлайн')).toBeVisible()
    expect(screen.getByText('1 200 МГц')).toBeVisible()
    expect(screen.getByText('CPU 52,5 °C')).toBeVisible()
    expect(screen.getByText(/SoC 48 °C · NVMe 41 °C/)).toBeVisible()
    expect(screen.getByText('−43 dBm')).toBeVisible()
    expect(screen.getByText('Ошибки: RX 2 · TX 3')).toBeVisible()
    expect(screen.getByText(/не доказывает снижение частоты из-за перегрева/i)).toBeVisible()
    expect(screen.getByText('Ожидание памяти и диска · 10 с')).toBeVisible()
    expect(screen.getByText(/Снимок:/)).toBeVisible()
  })

  it('names stale snapshots without showing old measurements', () => {
    render(<HostPerformance value={{ source_state: 'stale', sampled_at: null }} />)

    expect(screen.getByText(/измерение производительности устарело/i)).toBeVisible()
    expect(screen.queryByText(/МГц/)).not.toBeInTheDocument()
  })

  it('keeps an unavailable Wi-Fi error direction explicit', () => {
    render(<HostPerformance value={{
      source_state: 'reported', sampled_at: 1_799_999_900,
      cpu: { online_cores: null, frequency_mhz: null },
      pressure: { memory: { some_avg10: null, full_avg10: null }, io: { some_avg10: null, full_avg10: null } },
      temperatures_c: { cpu: null, gpu: null, soc: null, nvme: null },
      gpu: { frequency_mhz: null, load_percent: null },
      wifi: { interface_count: 1, signal_dbm: -43, rx_errors: 7, tx_errors: null },
    }} />)

    expect(screen.getByText('Ошибки: RX 7 · TX не измерено')).toBeVisible()
  })
})
