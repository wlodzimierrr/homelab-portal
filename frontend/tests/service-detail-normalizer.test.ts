import assert from 'node:assert/strict'
import test from 'node:test'
import {
  normalizeServiceCapabilities,
  normalizeServiceDetail,
  normalizeServiceProjectContext,
} from '../src/features/service-details/normalizers/service-detail-normalizer.js'

test('normalizeServiceDetail prefers backend project context and capabilities when present', () => {
  const normalized = normalizeServiceDetail({
    serviceId: 'homelab-api',
    serviceDetail: {
      projectContext: {
        projectId: 'project-1',
        projectName: 'Homelab',
        namespace: 'homelab-prod',
        siblingServiceIds: ['homelab-api', 'homelab-web'],
        isLinked: true,
      },
      capabilities: {
        canDeployToDev: false,
        canPromoteToProd: false,
        canRollback: false,
        rollbackEnvs: [],
        canEditConfig: false,
        configEnvs: [],
        canEditPublicHostname: false,
        canAdopt: false,
        canDelete: false,
        decommissionMode: 'unsupported',
        decommissionReason: 'Shared service',
      },
    },
    catalogRow: {
      env: 'prod',
      namespace: 'fallback-namespace',
      projectId: 'fallback-project',
      projectName: 'Fallback',
      primaryServiceId: 'homelab-api',
      serviceIds: ['homelab-api', 'homelab-worker'],
      services: [],
      appLabel: 'fallback-app',
      joinSource: 'primary_key',
      serviceCount: 2,
    },
  })

  assert.deepEqual(normalized.projectContext, {
    projectId: 'project-1',
    projectName: 'Homelab',
    namespace: 'homelab-prod',
    siblingServiceIds: ['homelab-web'],
    isLinked: true,
  })
  assert.equal(normalized.capabilities.canDeployToDev, false)
  assert.equal(normalized.capabilities.canPromoteToProd, false)
  assert.equal(normalized.capabilities.canRollback, false)
  assert.deepEqual(normalized.capabilities.rollbackEnvs, [])
  assert.equal(normalized.capabilities.canEditConfig, false)
  assert.deepEqual(normalized.capabilities.configEnvs, [])
  assert.equal(normalized.capabilities.canEditPublicHostname, false)
  assert.equal(normalized.capabilities.canAdopt, false)
  assert.equal(normalized.capabilities.canDelete, false)
  assert.equal(normalized.capabilities.decommissionMode, 'unsupported')
  assert.equal(normalized.capabilities.decommissionReason, 'Shared service')
})

test('normalizeServiceDetail still falls back to catalog project context when backend projectContext is absent', () => {
  const normalized = normalizeServiceDetail({
    serviceId: 'homelab-api',
    catalogRow: {
      env: 'prod',
      namespace: 'homelab-prod',
      projectId: 'project-1',
      projectName: 'Homelab',
      primaryServiceId: 'homelab-api',
      serviceIds: ['homelab-api', 'homelab-web'],
      services: [],
      appLabel: 'homelab-api',
      joinSource: 'primary_key',
      serviceCount: 2,
    },
  })

  assert.deepEqual(normalized.projectContext, {
    projectId: 'project-1',
    projectName: 'Homelab',
    namespace: 'homelab-prod',
    siblingServiceIds: ['homelab-web'],
    isLinked: true,
  })
  assert.equal(normalized.capabilities.canDeployToDev, false)
  assert.equal(normalized.capabilities.canPromoteToProd, false)
  assert.equal(normalized.capabilities.canRollback, false)
  assert.deepEqual(normalized.capabilities.rollbackEnvs, [])
  assert.equal(normalized.capabilities.canEditConfig, false)
  assert.deepEqual(normalized.capabilities.configEnvs, [])
  assert.equal(normalized.capabilities.canEditPublicHostname, false)
  assert.equal(normalized.capabilities.canAdopt, false)
  assert.equal(normalized.capabilities.canDelete, false)
  assert.equal(normalized.capabilities.decommissionMode, 'unsupported')
  assert.equal(normalized.capabilities.decommissionReason, null)
})

test('normalizeServiceCapabilities keeps adopt fallback permissive when project context is absent', () => {
  const normalized = normalizeServiceCapabilities(null, null)

  assert.equal(normalized.canDeployToDev, false)
  assert.equal(normalized.canPromoteToProd, false)
  assert.equal(normalized.canRollback, false)
  assert.equal(normalized.canEditConfig, false)
  assert.equal(normalized.canEditPublicHostname, false)
  assert.equal(normalized.canAdopt, true)
  assert.equal(normalized.canDelete, false)
  assert.equal(normalized.decommissionMode, 'unsupported')
  assert.equal(normalized.decommissionReason, null)
})

test('normalizeServiceProjectContext returns null when neither backend nor catalog context exists', () => {
  const projectContext = normalizeServiceProjectContext({
    serviceId: 'unknown-service',
    serviceDetail: null,
    catalogRow: null,
  })

  assert.equal(projectContext, null)
})
