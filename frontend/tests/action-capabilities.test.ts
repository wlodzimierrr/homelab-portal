import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveServiceActionCapabilities } from '../src/features/service-details/action-capabilities.js'

test('backend capabilities can keep forward actions visible even when rollback is false', () => {
  const resolved = resolveServiceActionCapabilities('homelab-api', {
    canDeployToDev: true,
    canPromoteToProd: true,
    canRollback: false,
    rollbackEnvs: [],
    canEditConfig: false,
    configEnvs: [],
    canEditPublicHostname: false,
    canAdopt: false,
  })

  assert.equal(resolved.canDeployToDev, true)
  assert.equal(resolved.canPromoteToProd, true)
  assert.equal(resolved.canRollback, false)
  assert.deepEqual(resolved.rollbackEnvs, [])
})
