const serviceSettingsRegex = /^\/services\/([^/]+)\/settings$/
const serviceDeploymentsRegex = /^\/services\/([^/]+)\/deployments$/
const serviceDetailsRegex = /^\/services\/([^/]+)$/

// Route helpers live here because the app shell does its own pathname matching.
// More specific service sub-routes must be checked before the generic service-details route.
export function isServiceDetailsPath(pathname: string) {
  return serviceDetailsRegex.test(pathname)
}

export function isServiceSettingsPath(pathname: string) {
  return serviceSettingsRegex.test(pathname)
}

export function isServiceDeploymentsPath(pathname: string) {
  return serviceDeploymentsRegex.test(pathname)
}

export function getServiceIdFromPath(pathname: string) {
  const settingsMatch = pathname.match(serviceSettingsRegex)
  if (settingsMatch?.[1]) {
    return settingsMatch[1]
  }

  const deploymentsMatch = pathname.match(serviceDeploymentsRegex)
  if (deploymentsMatch?.[1]) {
    return deploymentsMatch[1]
  }

  const detailsMatch = pathname.match(serviceDetailsRegex)
  if (detailsMatch?.[1]) {
    return detailsMatch[1]
  }

  return ''
}
