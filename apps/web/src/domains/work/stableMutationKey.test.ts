import { expect, it, vi } from 'vitest'
import { StableMutationKey } from './stableMutationKey'

it('keeps one key for an uncertain retry and rotates after success or payload change', () => {
  const uuid = vi.fn().mockReturnValueOnce('key-a').mockReturnValueOnce('key-b').mockReturnValueOnce('key-c')
  const holder = new StableMutationKey(uuid)
  expect(holder.get('message', 'text-a')).toBe('key-a')
  expect(holder.get('message', 'text-a')).toBe('key-a')
  expect(holder.get('message', 'text-b')).toBe('key-b')
  holder.succeeded('message', 'text-b')
  expect(holder.get('message', 'text-b')).toBe('key-c')
})
