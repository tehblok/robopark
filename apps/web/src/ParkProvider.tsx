import { useMemo, type PropsWithChildren } from 'react'
import { ParkScopeProvider } from './app/park/ParkScopeProvider'
import { useParkScope } from './app/park/parkScope'
import { ParkContext } from './park-context'

function LegacyParkContextBridge({ children }: PropsWithChildren) {
  const { parkId, setParkId, parks, loading, locked } = useParkScope()
  const value = useMemo(
    () => ({
      parkId,
      setParkId,
      parks,
      parksLoading: loading,
      parkLocked: locked,
    }),
    [parkId, setParkId, parks, loading, locked],
  )

  return <ParkContext.Provider value={value}>{children}</ParkContext.Provider>
}

export function ParkProvider({ children }: PropsWithChildren) {
  return (
    <ParkScopeProvider>
      <LegacyParkContextBridge>{children}</LegacyParkContextBridge>
    </ParkScopeProvider>
  )
}
