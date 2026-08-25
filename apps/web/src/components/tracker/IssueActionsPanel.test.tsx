import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ru } from '../../i18n/ru'
import { IssueActionsPanel } from './IssueActionsPanel'

const noop = async () => {}

const baseProps = {
  canWrite: true,
  transitions: [{ id: 'resolve', display: 'Resolve' }],
  onAssign: noop,
  onUnassign: noop,
  onTransition: noop,
  onClose: noop,
}

describe('IssueActionsPanel', () => {
  it('shows an error alert when a comment request fails', async () => {
    render(
      <IssueActionsPanel
        {...baseProps}
        onComment={async () => {
          throw new Error('network')
        }}
      />,
    )

    fireEvent.change(screen.getByLabelText(ru.tracker.comments), {
      target: { value: 'Need help' },
    })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    await waitFor(() => {
      expect(screen.getByText(ru.tracker.actions.failed)).toBeInTheDocument()
    })
    expect(screen.getByText(ru.tracker.actions.failed)).toHaveClass('alert-error')
  })

  it('disables action buttons while a request is in flight', async () => {
    let resolveComment: (() => void) | undefined
    render(
      <IssueActionsPanel
        {...baseProps}
        onComment={() =>
          new Promise<void>((resolve) => {
            resolveComment = resolve
          })
        }
      />,
    )

    fireEvent.change(screen.getByLabelText(ru.tracker.comments), {
      target: { value: 'Working…' },
    })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    expect(screen.getByRole('button', { name: ru.loading })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeDisabled()

    resolveComment?.()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: ru.loading })).not.toBeInTheDocument()
    })
  })

  it('does not render write actions when canWrite is false', () => {
    render(
      <IssueActionsPanel
        {...baseProps}
        canWrite={false}
        issueUrl="https://st.yandex-team.ru/TEST-1"
        onComment={noop}
      />,
    )

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByText(ru.tracker.actionsDisabled)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: ru.tracker.actions.openInTracker })).toHaveAttribute(
      'href',
      'https://st.yandex-team.ru/TEST-1',
    )
  })
})
