import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { resolveAppRoute } from '../src/app/router.js'
import { getServiceIdFromPath, isServiceDeploymentsPath, isServiceOverviewPath } from '../src/lib/routes.js'

describe('service route helpers', () => {
  it('recognizes the current service overview route', () => {
    assert.equal(isServiceOverviewPath('/services/api-gateway'), true)
    assert.equal(isServiceDeploymentsPath('/services/api-gateway'), false)
    assert.equal(getServiceIdFromPath('/services/api-gateway'), 'api-gateway')
  })

  it('recognizes the current service deployments route', () => {
    assert.equal(isServiceDeploymentsPath('/services/api-gateway/deployments'), true)
    assert.equal(isServiceOverviewPath('/services/api-gateway/deployments'), false)
    assert.equal(getServiceIdFromPath('/services/api-gateway/deployments'), 'api-gateway')
  })
})

describe('resolveAppRoute', () => {
  it('resolves service overview routes before falling back', () => {
    assert.deepEqual(resolveAppRoute('/services/api-gateway'), {
      kind: 'service-overview',
      serviceId: 'api-gateway',
    })
  })

  it('resolves service deployments routes with the decoded service id', () => {
    assert.deepEqual(resolveAppRoute('/services/api-gateway/deployments'), {
      kind: 'service-deployments',
      serviceId: 'api-gateway',
    })
  })
})
