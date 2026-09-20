import { createContext, useContext } from 'react'
import type { InterfaceMode } from './interfaceModeStore'

export const PresentationModeContext = createContext<InterfaceMode | null>('classic')

export function usePresentationMode(): InterfaceMode | null {
  return useContext(PresentationModeContext)
}
