// Read the exact document policy, since the online-only terminal deliberately
// has a stricter CSP than the SPA. This fixture supports our literal location
// blocks only and rejects ambiguity instead of silently weakening a policy.
export function readProductionCsp(nginxConfig, documentPath) {
  const locations = [...nginxConfig.matchAll(/^\s*location\s+=\s+(\S+)\s*\{([^{}]*)^\s*\}/gm)]
    .filter(match => match[1] === documentPath)
  if (locations.length !== 1) throw new Error(`Expected one exact nginx location for ${documentPath}`)
  const values = [...locations[0][2].matchAll(/^\s*add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always;/gm)]
  if (values.length !== 1) throw new Error(`Expected one production Content-Security-Policy for ${documentPath}`)
  return values[0][1]
}
