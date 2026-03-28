import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveAppRoute } from '../src/app/router.js'
import { getServiceIdFromPath, isServiceDeploymentsPath, isServiceDetailsPath } from '../src/lib/routes.js'

test('resolveAppRoute still matches the service deployments route', () => {
  assert.deepEqual(resolveAppRoute('/services/homelab-api/deployments'), {
    kind: 'service-deployments',
    serviceId: 'homelab-api',
  })
  assert.equal(isServiceDeploymentsPath('/services/homelab-api/deployments'), true)
  assert.equal(getServiceIdFromPath('/services/homelab-api/deployments'), 'homelab-api')
})

test('resolveAppRoute still matches the overview route for generic service paths', () => {
  assert.deepEqual(resolveAppRoute('/services/homelab-api'), {
    kind: 'service-details',
    serviceId: 'homelab-api',
  })
  assert.equal(isServiceDetailsPath('/services/homelab-api'), true)
  assert.equal(getServiceIdFromPath('/services/homelab-api'), 'homelab-api')
})
