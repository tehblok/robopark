import { Dialog, type DialogProps } from './Dialog'

export type BottomSheetProps = DialogProps

export function BottomSheet({ children, ...props }: BottomSheetProps) {
  return (
    <Dialog {...props}>
      <div className="rp-bottom-sheet">{children}</div>
    </Dialog>
  )
}
