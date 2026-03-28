const serviceSettingsRegex = /^\/services\/([^/]+)\/settings$/
const serviceDeploymentsRegex = /^\/services\/([^/]+)\/deployments$/
const serviceOverviewRegex = /^\/services\/([^/]+)$/

// Route helpers live here because the app shell does its own pathname matching.
// The deployments route must be checked before the generic service-overview route.
export function isServiceOverviewPath(pathname: string) {
  return serviceOverviewRegex.test(pathname)
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

  const overviewMatch = pathname.match(serviceOverviewRegex)
  if (overviewMatch?.[1]) {
    return overviewMatch[1]
  }

  return ''
}
