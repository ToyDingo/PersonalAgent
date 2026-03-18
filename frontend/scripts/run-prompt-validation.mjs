import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const repoRoot = path.resolve(__dirname, '../..')
const fixturesPath = path.resolve(
  repoRoot,
  'frontend/src/contracts/fixtures/prompt-validation.v1.json'
)
const reportsDir = path.resolve(repoRoot, 'frontend/docs/reports')
const apiBaseUrl = process.env.PROMPT_VALIDATION_API_BASE_URL ?? 'http://127.0.0.1:8000'
const requestTimeoutMs = Number(process.env.PROMPT_VALIDATION_TIMEOUT_MS ?? 90000)
const uploadFixturePath = process.env.PROMPT_VALIDATION_UPLOAD_FILE_PATH

function assert(condition, message) {
  if (!condition) {
    throw new Error(message)
  }
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nowIso() {
  return new Date().toISOString()
}

function safeString(value) {
  if (typeof value === 'string') {
    return value
  }
  return String(value ?? '')
}

function collectToolNames(toolResults) {
  if (!Array.isArray(toolResults)) {
    return []
  }
  return toolResults
    .map((item) => (isRecord(item) ? safeString(item.name).trim() : ''))
    .filter(Boolean)
}

function validateResponseShape(response) {
  assert(isRecord(response), 'Response must be an object.')
  assert(response.result_type === 'calendar_events', 'Invalid result_type.')
  assert(typeof response.action === 'string', 'Missing action in response.')
  assert(Array.isArray(response.events), 'events must be an array.')
  assert(isRecord(response.summary), 'summary must be an object.')
  assert(isRecord(response.meta), 'meta must be an object.')
  assert(Array.isArray(response.tool_results), 'tool_results must be an array.')
}

async function timedPostJson(url, payload, timeoutMs) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  const startedAt = Date.now()
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    const elapsedMs = Date.now() - startedAt
    const text = await response.text()
    let json = null
    try {
      json = JSON.parse(text)
    } catch {
      // Keep raw body for diagnostics.
    }
    return { response, json, raw: text, elapsedMs }
  } finally {
    clearTimeout(timeoutId)
  }
}

async function timedPostFormData(url, formData, timeoutMs) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  const startedAt = Date.now()
  try {
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    })
    const elapsedMs = Date.now() - startedAt
    const text = await response.text()
    let json = null
    try {
      json = JSON.parse(text)
    } catch {
      // Keep raw body for diagnostics.
    }
    return { response, json, raw: text, elapsedMs }
  } finally {
    clearTimeout(timeoutId)
  }
}

async function assertBackendReachable() {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 5000)
  try {
    const response = await fetch(`${apiBaseUrl}/health`, {
      method: 'GET',
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(`Health check returned HTTP ${response.status}.`)
    }
  } catch (error) {
    const detail =
      error instanceof Error
        ? `${error.message}${error.cause ? ` | cause: ${String(error.cause)}` : ''}`
        : String(error)
    throw new Error(
      `Backend is unreachable at ${apiBaseUrl}. Start the backend first, then rerun prompt validation. Detail: ${detail}`
    )
  } finally {
    clearTimeout(timeoutId)
  }
}

function evaluateExpectations(response, elapsedMs, expectations) {
  const failures = []
  const action = safeString(response.action)
  const toolNames = collectToolNames(response.tool_results)
  const resolvedSource = safeString(
    isRecord(response.meta?.resolved_time_window)
      ? response.meta.resolved_time_window.source_phrase
      : ''
  ).toLowerCase()

  if (Array.isArray(expectations.action_in) && expectations.action_in.length > 0) {
    if (!expectations.action_in.includes(action)) {
      failures.push(
        `Expected action in [${expectations.action_in.join(', ')}], got "${action}".`
      )
    }
  }

  if (
    Array.isArray(expectations.required_tools_any) &&
    expectations.required_tools_any.length > 0
  ) {
    const hasAny = expectations.required_tools_any.some((tool) => toolNames.includes(tool))
    if (!hasAny) {
      failures.push(
        `Expected one of tools [${expectations.required_tools_any.join(
          ', '
        )}], saw [${toolNames.join(', ')}].`
      )
    }
  }

  if (
    Array.isArray(expectations.forbidden_tools_any) &&
    expectations.forbidden_tools_any.length > 0
  ) {
    const forbiddenUsed = expectations.forbidden_tools_any.filter((tool) =>
      toolNames.includes(tool)
    )
    if (forbiddenUsed.length > 0) {
      failures.push(`Forbidden tools used: [${forbiddenUsed.join(', ')}].`)
    }
  }

  if (typeof expectations.resolved_source_contains === 'string') {
    const expected = expectations.resolved_source_contains.toLowerCase()
    if (!resolvedSource.includes(expected)) {
      failures.push(
        `Expected resolved source phrase to include "${expectations.resolved_source_contains}", got "${resolvedSource || 'n/a'}".`
      )
    }
  }

  if (typeof expectations.max_duration_ms === 'number') {
    if (elapsedMs > expectations.max_duration_ms) {
      failures.push(
        `Duration ${elapsedMs}ms exceeded max ${expectations.max_duration_ms}ms.`
      )
    }
  }

  return {
    passed: failures.length === 0,
    failures,
    toolNames,
  }
}

async function runFollowup(caseId, followup, priorResponse) {
  if (!isRecord(followup) || safeString(followup.mode) !== 'cancel') {
    return null
  }
  const confirmationId = safeString(priorResponse?.summary?.confirmation_id)
  if (!confirmationId) {
    return {
      caseId,
      mode: followup.mode,
      skipped: true,
      passed: true,
      failures: [],
      note: 'No confirmation_id available; followup skipped.',
    }
  }

  const payload = {
    message: 'operation_confirmation',
    context: {
      operation_confirmation: {
        action: 'cancel',
        confirmation_id: confirmationId,
        selected_event_ids: [],
      },
      contract_version: 'v1',
      client_platform: 'desktop',
    },
  }

  const { response, json, raw, elapsedMs } = await timedPostJson(
    `${apiBaseUrl}/agent/chat`,
    payload,
    requestTimeoutMs
  )
  if (!response.ok) {
    return {
      caseId,
      mode: followup.mode,
      skipped: false,
      passed: false,
      elapsedMs,
      failures: [`Followup HTTP ${response.status}: ${raw}`],
    }
  }

  try {
    validateResponseShape(json)
  } catch (error) {
    return {
      caseId,
      mode: followup.mode,
      skipped: false,
      passed: false,
      elapsedMs,
      failures: [`Followup response shape invalid: ${error.message}`],
    }
  }

  const failures = []
  if (Array.isArray(followup.expect_action_in) && followup.expect_action_in.length > 0) {
    if (!followup.expect_action_in.includes(safeString(json.action))) {
      failures.push(
        `Expected followup action in [${followup.expect_action_in.join(', ')}], got "${safeString(
          json.action
        )}".`
      )
    }
  }

  return {
    caseId,
    mode: followup.mode,
    skipped: false,
    passed: failures.length === 0,
    elapsedMs,
    action: safeString(json.action),
    failures,
  }
}

async function runCase(testCase) {
  const startedAt = nowIso()
  const payload = {
    message: testCase.prompt,
    context: {
      ...(isRecord(testCase.context) ? testCase.context : {}),
      contract_version: 'v1',
      client_platform: 'desktop',
    },
  }

  const result = {
    id: safeString(testCase.id),
    required: testCase.required !== false,
    prompt: safeString(testCase.prompt),
    started_at: startedAt,
    status: 'failed',
    elapsed_ms: null,
    action: null,
    tool_names: [],
    failures: [],
    followup: null,
  }

  try {
    const { response, json, raw, elapsedMs } = await timedPostJson(
      `${apiBaseUrl}/agent/chat`,
      payload,
      requestTimeoutMs
    )
    result.elapsed_ms = elapsedMs

    if (!response.ok) {
      result.failures.push(`HTTP ${response.status}: ${raw}`)
      return result
    }

    validateResponseShape(json)
    const evaluation = evaluateExpectations(
      json,
      elapsedMs,
      isRecord(testCase.expectations) ? testCase.expectations : {}
    )

    result.action = safeString(json.action)
    result.tool_names = evaluation.toolNames
    result.failures.push(...evaluation.failures)

    if (isRecord(testCase.followup)) {
      result.followup = await runFollowup(result.id, testCase.followup, json)
      if (result.followup && result.followup.passed === false) {
        result.failures.push(
          ...result.followup.failures.map((message) => `Followup: ${message}`)
        )
      }
    }

    result.status = result.failures.length === 0 ? 'passed' : 'failed'
    return result
  } catch (error) {
    if (error instanceof Error) {
      const suffix = error.cause ? ` | cause: ${String(error.cause)}` : ''
      result.failures.push(`${error.message}${suffix}`)
    } else {
      result.failures.push(String(error))
    }
    return result
  }
}

function summarize(results) {
  const requiredCases = results.filter((item) => item.required)
  const optionalCases = results.filter((item) => !item.required)
  const requiredFailed = requiredCases.filter((item) => item.status !== 'passed')
  const optionalFailed = optionalCases.filter((item) => item.status !== 'passed')
  const elapsed = results
    .map((item) => item.elapsed_ms)
    .filter((value) => typeof value === 'number')
    .sort((a, b) => a - b)

  const p50 =
    elapsed.length > 0 ? elapsed[Math.floor((elapsed.length - 1) * 0.5)] : null
  const p95 =
    elapsed.length > 0 ? elapsed[Math.floor((elapsed.length - 1) * 0.95)] : null

  return {
    total_cases: results.length,
    required_cases: requiredCases.length,
    optional_cases: optionalCases.length,
    required_failed: requiredFailed.length,
    optional_failed: optionalFailed.length,
    overall_passed: requiredFailed.length === 0,
    latency_ms: {
      min: elapsed.length ? elapsed[0] : null,
      p50,
      p95,
      max: elapsed.length ? elapsed[elapsed.length - 1] : null,
    },
  }
}

function buildMarkdownReport(report) {
  const lines = []
  lines.push('# Prompt Validation Baseline Report')
  lines.push('')
  lines.push(`- Generated at: ${report.generated_at}`)
  lines.push(`- API base URL: ${report.api_base_url}`)
  lines.push(`- Suite version: ${report.suite_version}`)
  lines.push(`- Overall pass (required cases): ${report.summary.overall_passed ? 'yes' : 'no'}`)
  lines.push(`- Required failures: ${report.summary.required_failed}`)
  lines.push(`- Optional failures: ${report.summary.optional_failed}`)
  lines.push(
    `- Latency ms (min/p50/p95/max): ${report.summary.latency_ms.min ?? 'n/a'}/${report.summary.latency_ms.p50 ?? 'n/a'}/${report.summary.latency_ms.p95 ?? 'n/a'}/${report.summary.latency_ms.max ?? 'n/a'}`
  )
  lines.push('')
  if (report.upload_probe) {
    lines.push('## Upload Probe')
    lines.push('')
    lines.push(`- attempted: yes`)
    lines.push(`- passed: ${report.upload_probe.passed ? 'yes' : 'no'}`)
    if (typeof report.upload_probe.elapsed_ms === 'number') {
      lines.push(`- elapsed: ${report.upload_probe.elapsed_ms}ms`)
    }
    if (typeof report.upload_probe.note === 'string') {
      lines.push(`- note: ${report.upload_probe.note}`)
    }
    for (const failure of report.upload_probe.failures ?? []) {
      lines.push(`- issue: ${failure}`)
    }
    lines.push('')
  }
  lines.push('## Case Results')
  lines.push('')
  for (const item of report.results) {
    lines.push(
      `- [${item.status === 'passed' ? 'PASS' : 'FAIL'}] ${item.id} (${item.required ? 'required' : 'optional'}) action=${item.action ?? 'n/a'} elapsed=${item.elapsed_ms ?? 'n/a'}ms tools=[${item.tool_names.join(', ')}]`
    )
    if (item.followup) {
      lines.push(
        `  - followup(${item.followup.mode}): ${item.followup.passed ? 'PASS' : 'FAIL'} action=${item.followup.action ?? 'n/a'} elapsed=${item.followup.elapsedMs ?? 'n/a'}ms${item.followup.skipped ? ' (skipped)' : ''}`
      )
    }
    for (const failure of item.failures) {
      lines.push(`  - issue: ${failure}`)
    }
  }
  lines.push('')
  return `${lines.join('\n')}\n`
}

async function runUploadProbe() {
  if (!uploadFixturePath) {
    return null
  }
  try {
    const fileBuffer = await readFile(uploadFixturePath)
    const fileName = path.basename(uploadFixturePath)
    const formData = new FormData()
    formData.append('file', new Blob([fileBuffer]), fileName)

    const uploadResponse = await timedPostFormData(
      `${apiBaseUrl}/agent/uploads`,
      formData,
      requestTimeoutMs
    )
    if (!uploadResponse.response.ok || !isRecord(uploadResponse.json)) {
      return {
        attempted: true,
        passed: false,
        elapsed_ms: uploadResponse.elapsedMs,
        failures: [
          `Upload failed: HTTP ${uploadResponse.response.status} body=${uploadResponse.raw}`,
        ],
      }
    }
    const uploadId = safeString(uploadResponse.json.upload_id)
    if (!uploadId) {
      return {
        attempted: true,
        passed: false,
        elapsed_ms: uploadResponse.elapsedMs,
        failures: ['Upload succeeded but upload_id was missing.'],
      }
    }

    const analyzeResponse = await timedPostJson(
      `${apiBaseUrl}/agent/uploads/${encodeURIComponent(uploadId)}/analyze`,
      { timezone: 'UTC', message: 'Add all events in this document to my calendar' },
      requestTimeoutMs
    )
    if (!analyzeResponse.response.ok || !isRecord(analyzeResponse.json)) {
      return {
        attempted: true,
        passed: false,
        elapsed_ms: uploadResponse.elapsedMs + analyzeResponse.elapsedMs,
        failures: [
          `Analyze failed: HTTP ${analyzeResponse.response.status} body=${analyzeResponse.raw}`,
        ],
      }
    }

    try {
      validateResponseShape(analyzeResponse.json)
    } catch (error) {
      return {
        attempted: true,
        passed: false,
        elapsed_ms: uploadResponse.elapsedMs + analyzeResponse.elapsedMs,
        failures: [
          `Analyze response shape invalid: ${error instanceof Error ? error.message : String(error)}`,
        ],
      }
    }

    return {
      attempted: true,
      passed: true,
      elapsed_ms: uploadResponse.elapsedMs + analyzeResponse.elapsedMs,
      note: `upload_id=${uploadId}`,
      failures: [],
    }
  } catch (error) {
    return {
      attempted: true,
      passed: false,
      failures: [error instanceof Error ? error.message : String(error)],
    }
  }
}

async function run() {
  await assertBackendReachable()

  const raw = await readFile(fixturesPath, 'utf8')
  const fixture = JSON.parse(raw)
  assert(Array.isArray(fixture.cases), 'Fixture must contain cases array.')

  const results = []
  for (const testCase of fixture.cases) {
    // Serial execution avoids interleaved confirmation state collisions.
    // This also produces cleaner latency metrics for debugging.
    const result = await runCase(testCase)
    results.push(result)
  }

  const report = {
    generated_at: nowIso(),
    suite_version: safeString(fixture.suite_version || 'unknown'),
    api_base_url: apiBaseUrl,
    request_timeout_ms: requestTimeoutMs,
    summary: summarize(results),
    results,
    upload_probe: null,
  }

  const uploadProbe = await runUploadProbe()
  if (uploadProbe) {
    report.upload_probe = uploadProbe
  }

  await mkdir(reportsDir, { recursive: true })
  const jsonPath = path.join(reportsDir, 'prompt-validation.latest.json')
  const mdPath = path.join(reportsDir, 'prompt-validation.latest.md')
  await writeFile(jsonPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  await writeFile(mdPath, buildMarkdownReport(report), 'utf8')

  console.log(`Prompt validation report written:`)
  console.log(`- ${jsonPath}`)
  console.log(`- ${mdPath}`)
  console.log(
    `Summary: required_failed=${report.summary.required_failed}, optional_failed=${report.summary.optional_failed}, overall_passed=${report.summary.overall_passed}`
  )

  if (!report.summary.overall_passed || (report.upload_probe && !report.upload_probe.passed)) {
    process.exitCode = 1
  }
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
})
