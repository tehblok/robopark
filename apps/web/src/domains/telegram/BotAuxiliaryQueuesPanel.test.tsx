import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BotAuxiliaryQueuesPanel } from './BotAuxiliaryQueuesPanel'
import type { BotConfigClient, BotConfigDocument } from '../system/botConfigApi'

const revision = 'a'.repeat(64)
const document = {
  sections: {
    roles: { revision, value: {} }, users: { revision, value: {} }, locations: { revision, value: {} },
    schedules: { revision, value: {} }, broadcasts: { revision, value: {} }, campaigns: { revision, value: {} },
    auxiliary_tracker_queues: { revision, value: ['ROBOMAINT'] }, profile: { revision, value: 'prod' },
    dispatcher_pause: { revision, value: false }, send_pause: { revision, value: false },
  },
} satisfies BotConfigDocument

describe('BotAuxiliaryQueuesPanel', () => {
  it('edits only the two supported queues and saves with the section revision', async () => {
    const client: BotConfigClient = {
      read: vi.fn().mockResolvedValue(document),
      update: vi.fn().mockResolvedValue({ revision: 'b'.repeat(64), value: ['ROBOMAINT', 'SDCWH'] }),
    }
    render(<BotAuxiliaryQueuesPanel client={client} />)
    expect(await screen.findByRole('checkbox', { name: 'ROBOMAINT' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Сохранить очереди' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'SDCWH' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить очереди' }))
    await waitFor(() => expect(client.update).toHaveBeenCalledWith(
      'auxiliary_tracker_queues', ['ROBOMAINT', 'SDCWH'], revision,
    ))
  })
})
