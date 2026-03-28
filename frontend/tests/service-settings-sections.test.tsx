import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { ServiceSettingsSections } from '../src/features/service-settings/components/settings-sections.js'
import { installTestWindow, render } from './test-setup.js'

test('ServiceSettingsSections renders the moved settings-owned sections', () => {
  installTestWindow('/services/homelab-api/settings')
  const markup = render(
    React.createElement(ServiceSettingsSections, {
      configSupported: true,
      publicHostEditMode: false,
      setPublicHostEditMode() {},
      publicHostValue: 'api.homelab.local',
      setPublicHostValue() {},
      publicHostSubmitting: false,
      publicHostError: '',
      publicHostResult: null,
      onSubmitPublicHostname() {},
      configEnv: 'dev',
      setConfigEnv() {},
      configEntries: [
        {
          key: 'LOG_LEVEL',
          value: 'info',
          allowedValues: ['debug', 'info'],
        },
      ],
      configLoading: false,
      configError: '',
      configSelectedValues: { LOG_LEVEL: 'info' },
      setConfigSelectedValues() {},
      configSubmitting: false,
      configSubmitError: '',
      configSubmitResult: null,
      onSubmitConfigEdit() {},
      onReloadConfig() {},
      adoptSupported: true,
      adoptProjectId: '',
      setAdoptProjectId() {},
      adoptSubmitting: false,
      adoptError: '',
      adoptResult: null,
      onSubmitAdopt() {},
    }),
  )

  assert.match(markup, /Public hostname/)
  assert.match(markup, /Runtime Config/)
  assert.match(markup, /Adopt into Project/)
})
