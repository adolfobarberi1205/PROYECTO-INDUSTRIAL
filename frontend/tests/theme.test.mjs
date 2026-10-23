import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const bootstrap = readFileSync(new URL('../public/theme-init.js', import.meta.url), 'utf8')

function initialize(getItem) {
  const document = { documentElement: { dataset: {} } }
  vm.runInNewContext(bootstrap, { document, localStorage: { getItem } })
  return document.documentElement.dataset.theme
}

test('preserves dark mode for a first visit', () => {
  assert.equal(initialize(() => null), 'dark')
})
test('restores light mode before rendering', () => {
  assert.equal(initialize(key => key === 'etl_theme' ? 'light' : null), 'light')
})
test('restores dark mode and rejects unknown preferences', () => {
  assert.equal(initialize(() => 'dark'), 'dark')
  assert.equal(initialize(() => 'invalid'), 'dark')
})
test('still opens with blocked storage', () => {
  assert.equal(initialize(() => { throw new Error('Storage blocked') }), 'dark')
})
