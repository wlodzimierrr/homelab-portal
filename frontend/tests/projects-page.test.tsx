import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { ProjectsPage } from '../src/pages/projects-page.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('ProjectsPage uses diagnostics-oriented framing', () => {
  const browser = installMockBrowser({ pathname: '/projects' })
  const markup = renderToHtml(createElement(ProjectsPage))

  assert.match(markup, /Catalog Diagnostics/)
  assert.match(markup, /GitOps catalog status, reconciliation signals, and project-to-service linkage details/)
  assert.match(markup, /This page is for catalog and registry diagnostics/)
  assert.doesNotMatch(markup, /GitOps-owned application projects and ownership details/)
  browser.cleanup()
})
