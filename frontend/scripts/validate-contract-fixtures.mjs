import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const fixturesDir = path.resolve(__dirname, '../src/contracts/fixtures')

async function readJson(name) {
  const fullPath = path.join(fixturesDir, name)
  const raw = await readFile(fullPath, 'utf8')
  return JSON.parse(raw)
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message)
  }
}

function validateRequestFixture(value, expectedPlatform) {
  assert(typeof value.message === 'string' && value.message.length > 0, 'Request message missing.')
  assert(value.context && typeof value.context === 'object', 'Request context missing.')
  assert(value.context.contract_version === 'v1', 'Request contract_version must be v1.')
  assert(
    value.context.client_platform === expectedPlatform,
    `Request client_platform must be ${expectedPlatform}.`
  )
}

function validateResponseFixture(value) {
  assert(value && typeof value === 'object', 'Response fixture must be an object.')
  assert(value.result_type === 'calendar_events', 'Invalid result_type.')
  assert(typeof value.action === 'string', 'Response action missing.')
  assert(Array.isArray(value.events), 'Response events must be an array.')
  assert(value.meta && typeof value.meta === 'object', 'Response meta missing.')
  assert(Array.isArray(value.tool_results), 'Response tool_results must be an array.')
}

function validateParity(desktopRequest, mobileRequest) {
  assert(desktopRequest.message === mobileRequest.message, 'Desktop/mobile fixture messages differ.')

  const desktopContext = { ...desktopRequest.context }
  const mobileContext = { ...mobileRequest.context }
  delete desktopContext.client_platform
  delete mobileContext.client_platform

  assert(
    JSON.stringify(desktopContext) === JSON.stringify(mobileContext),
    'Desktop/mobile request context differs beyond platform.'
  )
}

async function run() {
  const desktopRequest = await readJson('agent-request.desktop.v1.json')
  const mobileRequest = await readJson('agent-request.mobile.v1.json')
  const pendingResponse = await readJson('agent-response.v1.json')
  const createResponse = await readJson('agent-response.create.v1.json')

  validateRequestFixture(desktopRequest, 'desktop')
  validateRequestFixture(mobileRequest, 'mobile')
  validateParity(desktopRequest, mobileRequest)
  validateResponseFixture(pendingResponse)
  validateResponseFixture(createResponse)

  console.log('Contract fixtures validated: desktop/mobile parity and response shape are good.')
}

run().catch((error) => {
  console.error(error.message)
  process.exit(1)
})

