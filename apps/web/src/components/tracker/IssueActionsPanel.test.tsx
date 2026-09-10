import { readFileSync } from 'node:fs'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type TrackerIssueCapabilities } from '../../api'
import { ru } from '../../i18n/ru'
import { IssueActionsPanel } from './IssueActionsPanel'

const noop = async () => {}
const workCss = readFileSync('src/domains/work/work.css', 'utf8')

function declaredStyles(element: Element, mediaCondition?: string): Record<string, string> {
  const style = document.createElement('style')
  style.textContent = workCss
  document.head.append(style)

  const sheet = style.sheet as CSSStyleSheet
  const rules = mediaCondition
    ? Array.from(sheet.cssRules).find(
        (rule): rule is CSSMediaRule =>
          'conditionText' in rule && rule.conditionText === mediaCondition,
      )?.cssRules ?? []
    : sheet.cssRules
  const declarations: Record<string, string> = {}

  for (const rule of Array.from(rules)) {
    if (!('selectorText' in rule && 'style' in rule)) continue
    const styleRule = rule as CSSStyleRule
    if (!element.matches(styleRule.selectorText)) continue
    for (const property of Array.from(styleRule.style)) {
      declarations[property] = styleRule.style.getPropertyValue(property)
    }
  }

  style.remove()
  return declarations
}

const disabledCapabilities: TrackerIssueCapabilities = {
  comment: false,
  assign: false,
  unassign: false,
  transition: false,
  close: false,
  attach: false,
}

const baseProps = {
  canWrite: true,
  transitions: [{ id: 'resolve', display: 'Resolve' }],
  onComment: noop,
  onAssign: noop,
  onUnassign: noop,
  onTransition: noop,
  onClose: noop,
}

describe('IssueActionsPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('keeps the phone comment and photo composer visible while secondary actions stay collapsed', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))

    render(<IssueActionsPanel {...baseProps} onAttach={noop} />)

    expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toBeVisible()
    expect(screen.getByText(ru.tracker.attachPhoto)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Статус задачи' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: ru.tracker.fields.assignee })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.assign })).not.toBeInTheDocument()
  })

  it.each(['close', 'comment'] as const)('keeps safe %s failure copy and request ID in one alert', async action => {
    const failure = async () => { throw new ApiError(409, 'tracker_transition_invalid', 'action-conflict-42') }
    render(<IssueActionsPanel {...baseProps} onClose={failure} onComment={failure} />)
    if (action === 'close') {
      fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
      fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
    } else {
      fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Проверено' } })
      fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    }
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Этот переход недоступен для тикета.')
    expect(alert).toHaveTextContent('Код запроса: action-conflict-42')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    if (action === 'close') expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

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

  it('uploads a selected photo when attach handler is provided', async () => {
    const uploads: File[] = []
    render(
      <IssueActionsPanel
        {...baseProps}
        onAttach={async (file) => {
          uploads.push(file)
        }}
        onComment={noop}
      />,
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['img'], 'break.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    fireEvent.click(screen.getByRole('button', { name: ru.tracker.attachPhotoSubmit }))

    await waitFor(() => {
      expect(uploads).toHaveLength(1)
    })
    expect(uploads[0]?.name).toBe('break.jpg')
  })

  it('shows photo upload even when other write actions are disabled', () => {
    render(
      <IssueActionsPanel
        {...baseProps}
        canWrite={false}
        onAttach={noop}
        onComment={noop}
      />,
    )

    expect(screen.getByText(ru.tracker.attachPhoto)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByText(ru.tracker.actionsDisabled)).not.toBeInTheDocument()
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

  it('lets an explicit all-false capability object override legacy write props', () => {
    render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={disabledCapabilities}
        issueUrl="javascript:alert(1)"
        onAttach={noop}
      />,
    )

    expect(screen.getByText(ru.tracker.actionsDisabled)).toBeInTheDocument()
    expect(screen.queryByLabelText(ru.tracker.comments)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(ru.tracker.actions.assignPlaceholder)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.unassign })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
    expect(screen.queryByText(ru.tracker.attachPhoto)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: ru.tracker.actions.openInTracker })).not.toBeInTheDocument()
  })

  it.each<keyof TrackerIssueCapabilities>([
    'comment',
    'assign',
    'unassign',
    'transition',
    'close',
    'attach',
  ])('renders only the independently enabled %s capability', (enabled) => {
    render(
      <IssueActionsPanel
        {...baseProps}
        canWrite={false}
        capabilities={{ ...disabledCapabilities, [enabled]: true }}
        onAttach={noop}
      />,
    )

    const controls: Record<keyof TrackerIssueCapabilities, HTMLElement | null> = {
      comment: screen.queryByLabelText(ru.tracker.comments),
      assign: screen.queryByLabelText(ru.tracker.actions.assignPlaceholder),
      unassign: screen.queryByRole('button', { name: ru.tracker.actions.unassign }),
      transition: screen.queryByRole('button', { name: 'Resolve' }),
      close: screen.queryByRole('button', { name: ru.tracker.actions.close }),
      attach: screen.queryByText(ru.tracker.attachPhoto),
    }

    for (const [capability, control] of Object.entries(controls)) {
      if (capability === enabled) {
        expect(control).toBeInTheDocument()
      } else {
        expect(control).not.toBeInTheDocument()
      }
    }
  })

  it('cancels a pending Tracker user lookup when assign capability is lost', async () => {
    vi.useFakeTimers()
    const trackerUsers = vi.spyOn(api, 'trackerUsers').mockResolvedValue([
      { login: 'ivan', display: 'Ivan', source: 'park' },
    ])
    const { rerender } = render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, assign: true }}
      />,
    )

    fireEvent.change(screen.getByLabelText(ru.tracker.actions.assignPlaceholder), {
      target: { value: 'ivan' },
    })
    rerender(
      <IssueActionsPanel
        {...baseProps}
        capabilities={disabledCapabilities}
      />,
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })

    expect(trackerUsers).not.toHaveBeenCalled()
  })

  it('queries Tracker users only while assign capability is enabled', async () => {
    vi.useFakeTimers()
    const trackerUsers = vi.spyOn(api, 'trackerUsers').mockResolvedValue([
      { login: 'ivan', display: 'Ivan', source: 'park' },
    ])
    render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, assign: true }}
      />,
    )

    fireEvent.change(screen.getByLabelText(ru.tracker.actions.assignPlaceholder), {
      target: { value: '  ivan  ' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })

    expect(trackerUsers).toHaveBeenCalledOnce()
    expect(trackerUsers).toHaveBeenCalledWith('ivan')
  })

  it('stacks the assignment form without an intrinsic-width overflow on compact screens', () => {
    render(
      <div className="rp-workbench">
        <IssueActionsPanel
          {...baseProps}
          capabilities={{ ...disabledCapabilities, assign: true }}
        />
      </div>,
    )

    const input = screen.getByLabelText(ru.tracker.actions.assignPlaceholder)
    const form = input.closest('form')
    const submit = screen.getByRole('button', { name: ru.tracker.actions.assign })

    expect(form).not.toBeNull()
    const formStyles = declaredStyles(form!)
    expect(formStyles).toMatchObject({
      'flex-wrap': 'wrap',
      'max-width': '100%',
    })
    expect(Number.parseFloat(formStyles['min-width'] ?? '')).toBe(0)
    expect(Number.parseFloat(declaredStyles(input)['min-width'] ?? '')).toBe(0)
    expect(declaredStyles(form!, '(max-width: 599px)')).toMatchObject({
      display: 'grid',
      'grid-template-columns': 'minmax(0, 1fr)',
      width: '100%',
    })
    expect(declaredStyles(input, '(max-width: 599px)')).toMatchObject({ width: '100%' })
    expect(declaredStyles(submit, '(max-width: 599px)')).toMatchObject({ width: '100%' })
  })

  it('keeps one dialog alert after close failure and closes only after success', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const onClose = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new Error('upstream'))
      .mockResolvedValueOnce(undefined)
    render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, close: true }}
        issueKey="ROBOPARK-42"
        onClose={onClose}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
    expect(onClose).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    const dialog = screen.getByRole('alertdialog')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Подтвердить закрытие' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(ru.tracker.actions.failed)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Подтвердить закрытие' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('closes a pending confirmation when close capability is revoked', () => {
    const { rerender } = render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, close: true, comment: true }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()

    rerender(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, comment: true }}
      />,
    )

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('rejects invalid and oversized attachments without invoking the callback', () => {
    const onAttach = vi.fn<(_: File) => Promise<void>>(async () => {})
    render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, attach: true }}
        onAttach={onAttach}
      />,
    )
    const input = document.querySelector('input[type="file"]') as HTMLInputElement

    fireEvent.change(input, {
      target: { files: [new File(['text'], 'notes.txt', { type: 'text/plain' })] },
    })
    expect(screen.getByRole('alert')).toHaveTextContent(ru.tracker.attachPhotoInvalidType)
    expect(onAttach).not.toHaveBeenCalled()

    const oversized = new File(['image'], 'large.jpg', { type: 'image/jpeg' })
    Object.defineProperty(oversized, 'size', { value: 15 * 1024 * 1024 + 1 })
    fireEvent.change(input, { target: { files: [oversized] } })
    expect(screen.getByRole('alert')).toHaveTextContent(ru.tracker.attachPhotoTooLarge)
    expect(onAttach).not.toHaveBeenCalled()
  })

  it('revokes the attachment preview when attach capability is lost', async () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const { rerender } = render(
      <IssueActionsPanel
        {...baseProps}
        capabilities={{ ...disabledCapabilities, attach: true }}
        onAttach={noop}
      />,
    )
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(['image'], 'robot.jpg', { type: 'image/jpeg' })] },
    })
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce())
    expect(document.querySelector('.issue-attach-preview')).toHaveAttribute('src', 'blob:preview')

    rerender(
      <IssueActionsPanel
        {...baseProps}
        capabilities={disabledCapabilities}
      />,
    )

    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview'))
    expect(screen.queryByText(ru.tracker.attachPhoto)).not.toBeInTheDocument()
  })
})
