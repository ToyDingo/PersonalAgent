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

function validateUploadRequestFixture(value) {
  assert(value && typeof value === 'object', 'Upload request fixture must be an object.')
  assert(typeof value.upload_id === 'string' && value.upload_id.length > 0, 'upload_id is required.')
  assert(typeof value.message === 'string' && value.message.length > 0, 'upload analyze message is required.')
  assert(value.context && typeof value.context === 'object', 'Upload request context missing.')
  assert(value.context.contract_version === 'v1', 'Upload request contract_version must be v1.')
  assert(value.context.client_platform === 'desktop', 'Upload request client_platform must be desktop.')
}

function validateUploadErrorFixture(value) {
  assert(value && typeof value === 'object', 'Upload error fixture must be an object.')
  assert(typeof value.error === 'string' && value.error.length > 0, 'Upload error code missing.')
  assert(typeof value.message === 'string' && value.message.length > 0, 'Upload error message missing.')
}

function validateReauthResponseFixture(value) {
  validateResponseFixture(value)
  assert(value.action === 'reauthorization_required', 'Reauth fixture action must be reauthorization_required.')
  assert(value.summary && typeof value.summary === 'object', 'Reauth fixture summary missing.')
  assert(value.summary.requires_reauth === true, 'Reauth fixture requires_reauth must be true.')
  assert(value.summary.service === 'google_calendar', 'Reauth fixture service must be google_calendar.')
  assert(typeof value.summary.reauth_endpoint === 'string', 'Reauth fixture reauth_endpoint missing.')
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
  const reauthRequiredResponse = await readJson('agent-response.reauth-required.v1.json')
  const reauthDeclinedResponse = await readJson('agent-response.reauth-declined.v1.json')
  const reauthResumedResponse = await readJson('agent-response.reauth-resumed-success.v1.json')
  const uploadRequest = await readJson('agent-upload-request.desktop.v1.json')
  const uploadAnalysisResponse = await readJson('agent-upload-analysis-response.v1.json')
  const uploadPendingResponse = await readJson('agent-upload-confirmation-pending.v1.json')
  const uploadConfirmedResponse = await readJson('agent-upload-confirmed-success.v1.json')
  const uploadUnsupportedTypeError = await readJson('agent-upload-error-unsupported-type.v1.json')

  validateRequestFixture(desktopRequest, 'desktop')
  validateRequestFixture(mobileRequest, 'mobile')
  validateParity(desktopRequest, mobileRequest)
  validateResponseFixture(pendingResponse)
  validateResponseFixture(createResponse)
  validateReauthResponseFixture(reauthRequiredResponse)
  validateResponseFixture(reauthDeclinedResponse)
  validateResponseFixture(reauthResumedResponse)
  validateUploadRequestFixture(uploadRequest)
  validateResponseFixture(uploadAnalysisResponse)
  validateResponseFixture(uploadPendingResponse)
  validateResponseFixture(uploadConfirmedResponse)
  validateUploadErrorFixture(uploadUnsupportedTypeError)

  console.log(
    'Contract fixtures validated: desktop/mobile parity, response shape, reauth contract, and upload contract are good.'
  )
}

run().catch((error) => {
  console.error(error.message)
  process.exit(1)
})

