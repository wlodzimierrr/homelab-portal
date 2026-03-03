const serviceDeploymentsRegex = /^\/services\/([^/]+)\/deployments$/
const serviceDetailsRegex = /^\/services\/([^/]+)$/

export function isServiceDetailsPath(pathname: string) {
  return serviceDetailsRegex.test(pathname)
}

export function isServiceDeploymentsPath(pathname: string) {
  return serviceDeploymentsRegex.test(pathname)
}

export function getServiceIdFromPath(pathname: string) {
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
