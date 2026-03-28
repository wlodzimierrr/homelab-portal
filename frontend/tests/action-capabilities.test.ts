import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveServiceActionCapabilities } from '../src/features/service-details/action-capabilities.js'
import { normalizeServiceDetail } from '../src/features/service-details/normalizers/service-detail-normalizer.js'

test('backend capabilities can keep forward actions visible even when rollback is false', () => {
  const normalized = normalizeServiceDetail({
    serviceId: 'homelab-api',
    serviceDetail: {
      projectContext: null,
      capabilities: {
        canDeployToDev: true,
        canPromoteToProd: true,
        canRollback: false,
        rollbackEnvs: [],
        canEditConfig: false,
        configEnvs: [],
        canEditPublicHostname: false,
        canAdopt: false,
      },
    },
  })
  const resolved = resolveServiceActionCapabilities(normalized.capabilities)

  assert.equal(resolved.canDeployToDev, true)
  assert.equal(resolved.canPromoteToProd, true)
  assert.equal(resolved.canRollback, false)
  assert.deepEqual(resolved.rollbackEnvs, [])
})
