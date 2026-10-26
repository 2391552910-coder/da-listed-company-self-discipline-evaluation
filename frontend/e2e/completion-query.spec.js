import { test, expect } from '@playwright/test'

// Real browser/API/worker with explicitly synthetic source files and provider.
// These tests verify navigation, query scope and provenance, not model quality.
const fixtureStock = '019997'
const inputOf = (page, id) => page.locator(`input[data-testid="${id}"], [data-testid="${id}"] input`).first()

async function getData(request, path, params = {}) {
  const response = await request.get('/api/v1' + path, { params })
  expect(response.ok(), `${path}: HTTP ${response.status()}`).toBeTruthy()
  const body = await response.json()
  expect(body.code).toBe(0)
  return body.data
}

async function postData(request, path, data, key) {
  const response = await request.post('/api/v1' + path, {
    data, ...(key ? { headers: { 'Idempotency-Key': key } } : {}),
  })
  expect(response.ok(), `${path}: HTTP ${response.status()}`).toBeTruthy()
  const body = await response.json()
  expect(body.code).toBe(0)
  return body.data
}

function waitForQuery(page, path, params) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return response.request().method() === 'GET' && url.pathname === '/api/v1' + path &&
      Object.entries(params).every(([key, value]) => url.searchParams.get(key) === String(value))
  })
}

async function selectRemoteCompany(page, id, query, label) {
  const control = page.getByTestId(id)
  await control.click()
  const response = waitForQuery(page, '/companies', { q: query, page_size: 50 })
  await inputOf(page, id).fill(query)
  expect((await response).ok()).toBeTruthy()
  await page.getByRole('option', { name: label, exact: true }).click()
  await expect(control).toContainText(label)
}

test('company paging and remote identity selection preserve company and year scope', async ({ page }) => {
  test.setTimeout(120000)
  const prefix = '分页验收合成公司-'
  const companies = []
  for (let index = 0; index < 52; index++) {
    companies.push(await postData(page.request, '/companies', {
      stock_code: String(980100 + index), stock_name: prefix + String(index).padStart(2, '0'),
    }))
  }
  const target = companies.at(-1)
  const first50 = await getData(page.request, '/companies', { page: 1, page_size: 50 })
  expect(first50.items.some(company => company.id === target.id)).toBe(false)
  const lastPage = await getData(page.request, '/companies', { q: prefix, page: 3, page_size: 20 })
  expect(lastPage.total).toBe(52)
  expect(lastPage.items).toHaveLength(12)
  expect(lastPage.items.at(-1).id).toBe(target.id)

  await page.goto('/companies')
  await inputOf(page, 'company-search').fill(prefix)
  await page.getByTestId('company-search-submit').click()
  await expect(page.getByTestId('company-total')).toContainText('共 52 家匹配公司')
  const thirdPage = waitForQuery(page, '/companies', { q: prefix, page: 3, page_size: 20 })
  await page.getByTestId('company-pagination').locator('.el-pager').getByText('3', { exact: true }).click()
  expect((await thirdPage).ok()).toBeTruthy()
  await expect(page.getByTestId('company-table')).toContainText(target.stock_name)
  await expect(page.getByTestId('company-table').locator('.el-table__body-wrapper tbody tr')).toHaveCount(12)

  // A permitted exchange suffix must hydrate the canonical registered company,
  // even though that company was not part of the initial 50-option response.
  await page.goto('/evaluations/new?stock_code=' + target.stock_code + '.SZ')
  const targetLabel = target.stock_code + ' ' + target.stock_name
  await expect(page.getByTestId('evaluation-company')).toContainText(targetLabel)
  await selectRemoteCompany(page, 'evaluation-company', target.stock_code, targetLabel)

  const fixture = (await getData(page.request, '/companies', { stock_code: fixtureStock })).items[0]
  expect(fixture).toBeTruthy()
  const empty = await getData(page.request, '/dashboard', { company_id: target.id, year: 2024 })
  expect(empty.counts.annual_documents).toBe(0)
  expect(empty.counts.ready_indexes).toBe(0)
  expect(empty.counts.evaluation_runs).toBe(0)
  expect(empty.coverage.dimensions.every(dimension => dimension.total_answers === 0)).toBe(true)
  await page.goto(`/?company_id=${target.id}&year=2024`)
  await expect(page.getByTestId('dashboard-company')).toContainText(targetLabel)
  await expect(page.getByTestId('dashboard-count-annual_documents')).toHaveText('0')
  await expect(page.getByTestId('dashboard-count-regulation_documents')).toHaveText(String(empty.counts.regulation_documents))
  await selectRemoteCompany(page, 'dashboard-company', fixtureStock, fixture.stock_code + ' ' + fixture.stock_name)
  await page.getByTestId('dashboard-apply').click()
  await expect(page).toHaveURL(new RegExp(`company_id=${fixture.id}.*year=2024`))
  await expect(page.getByTestId('dashboard-count-annual_documents')).toHaveText('1')
  await expect(page.getByTestId('dashboard-count-ready_indexes')).toHaveText('1')
  await inputOf(page, 'dashboard-year').fill('2023')
  await page.getByTestId('dashboard-apply').click()
  await expect(page).toHaveURL(/year=2023/)
  await expect(page.getByTestId('dashboard-count-annual_documents')).toHaveText('0')
  await expect(page.getByTestId('dashboard-count-ready_indexes')).toHaveText('0')
  await expect(page.getByTestId('dashboard-count-regulation_documents')).toHaveText(String(empty.counts.regulation_documents))
  await expect(page.getByTestId('dashboard-scope')).toContainText('2023 年报')
  await page.reload()
  await expect(page.getByTestId('dashboard-count-annual_documents')).toHaveText('0')
  await expect(page.getByTestId('dashboard-scope')).toContainText(fixtureStock)
  await expect(page.getByTestId('dashboard-coverage-note')).toContainText('不代表语义支持')
  await page.screenshot({ path: '../test-results/browser-company-dashboard-scope.png', fullPage: true })
})

test('document scope and the second chunk page retain the exact parse and original locator', async ({ page }) => {
  test.setTimeout(120000)
  const title = '片段翻页验收合成规则（非真实法规）'
  const text = Array.from({ length: 60 }, (_, index) =>
    `合成规则第${String(index).padStart(3, '0')}条：` + '本段仅用于核对分页、解析批次和字符定位，不构成真实法规或研究证据。'.repeat(4),
  ).join('\n')
  const uploaded = await page.request.post('/api/v1/documents/upload', { multipart: {
    corpus_type: 'regulation', title, metadata: '{}',
    file: { name: 'synthetic-chunk-pagination.txt', mimeType: 'text/plain', buffer: Buffer.from(text, 'utf8') },
  } })
  expect(uploaded.ok()).toBeTruthy()
  const version = (await uploaded.json()).data
  const task = await postData(page.request, `/document-versions/${version.id}/parse`, { chunk_chars: 100, overlap_chars: 0 })
  let job
  await expect.poll(async () => {
    job = await getData(page.request, '/jobs/' + task.id)
    return job.status
  }, { timeout: 60000 }).toBe('succeeded')
  const parseId = job.result.parse_id
  const second = await getData(page.request, `/document-versions/${version.id}/chunks`, { parse_id: parseId, page: 2, page_size: 20 })
  expect(second.total).toBeGreaterThan(20)
  expect(second.items.length).toBeGreaterThan(0)
  const expected = second.items[0]
  const fixture = (await getData(page.request, '/companies', { stock_code: fixtureStock })).items[0]
  await page.goto(`/documents?company_id=${fixture.id}&report_year=2023`)
  await expect(page.getByTestId('document-table')).toContainText('当前筛选下暂无文档')
  await page.getByTestId('document-filter-source').click()
  await page.getByRole('option', { name: '法规', exact: true }).click()
  await page.getByTestId('document-filter-apply').click()
  await expect(page.getByTestId('document-table')).toContainText(title)
  await expect(page.getByTestId('document-filter-company').locator('input')).toBeDisabled()
  await expect(inputOf(page, 'document-filter-year')).toBeDisabled()
  await page.getByTestId('document-table').getByText(title, { exact: true }).click()
  await page.getByTestId('chunks-open-' + parseId).click()
  await expect(page.getByTestId('chunks-identity')).toContainText(version.id)
  await expect(page.getByTestId('chunks-identity')).toContainText(parseId)
  await expect(page.getByTestId('chunks-table').locator('.el-table__body-wrapper tbody tr')).toHaveCount(20)
  const secondPage = waitForQuery(page, `/document-versions/${version.id}/chunks`, { parse_id: parseId, page: 2, page_size: 20 })
  await page.getByTestId('chunks-pagination').locator('.el-pager').getByText('2', { exact: true }).click()
  expect((await secondPage).ok()).toBeTruthy()
  const firstRow = page.getByTestId('chunks-table').locator('.el-table__body-wrapper tbody tr').first()
  await expect(firstRow).toContainText(expected.content)
  await firstRow.getByRole('button', { name: '核对位置', exact: true }).click()
  const source = page.getByRole('dialog', { name: '原文与定位核查', exact: true })
  await expect(source).toContainText(`[${expected.char_start}, ${expected.char_end})`)
  await expect(source.locator('mark')).toHaveText(expected.content)
  await expect(source).toContainText('原文件完整性与文本帧区间校验通过')
  const original = await getData(page.request, '/chunks/' + expected.id + '/source')
  expect(original.version_id).toBe(version.id)
  expect(original.frame_text.slice(original.char_start, original.char_end)).toBe(expected.content)
  await page.screenshot({ path: '../test-results/browser-document-chunk-page.png', fullPage: true })
})

test('evaluation mode and status filtering reset the second page without changing the creation form', async ({ page }) => {
  test.setTimeout(120000)
  const index = (await getData(page.request, '/indexes')).find(item => item.status === 'ready' && item.profile === 'bm25_diagnostic')
  expect(index).toBeTruthy()
  const tasks = []
  for (let number = 0; number < 12; number++) {
    const mode = number === 11 ? 'dual_agent' : 'dual_fixed'
    tasks.push(await postData(page.request, '/evaluations', {
      index_id: index.id, stock_code: fixtureStock, report_year: 2024, mode,
      questions: [{ key: 'completion-filter-' + number, dimension: 'D2', text: '依据合成年报与规则，审计委员会履职有哪些可观察依据与限制？' }],
      retrieval: { tokenizer_kind: 'utf8_diagnostic' }, budget: { max_model_calls: 4 },
      ...(mode === 'dual_agent' ? { agent: { max_rounds: 1, max_supplemental_calls: 1, allow_revision: true } } : {}),
    }, 'completion-query-mode-' + number))
  }
  await expect.poll(async () => {
    const jobs = await Promise.all(tasks.map(task => getData(page.request, '/jobs/' + task.id)))
    return jobs.every(job => job.status === 'succeeded')
  }, { timeout: 90000 }).toBe(true)

  await page.goto('/evaluations/new?stock_code=' + fixtureStock)
  const fixture = (await getData(page.request, '/companies', { stock_code: fixtureStock })).items[0]
  await expect(page.getByTestId('evaluation-company')).toContainText(fixture.stock_name)
  const creationQuestion = '此输入只保存在新建表单，筛选运行记录不应清除或提交。'
  await page.getByTestId('evaluation-question').fill(creationQuestion)
  await selectRemoteCompany(page, 'evaluation-filter-company', fixtureStock, fixture.stock_code + ' ' + fixture.stock_name)
  await inputOf(page, 'evaluation-filter-year').fill('2024')
  await page.getByTestId('evaluation-filter-mode').click()
  await page.getByRole('option', { name: '固定双源', exact: true }).click()
  await page.getByTestId('evaluation-filter-status').click()
  await page.getByRole('option', { name: '已完成', exact: true }).click()
  const fixedParams = { stock_code: fixtureStock, report_year: 2024, mode: 'dual_fixed', status: 'succeeded', page: 1, page_size: 10 }
  const fixedResponse = waitForQuery(page, '/evaluations', fixedParams)
  await page.getByTestId('evaluation-filter-apply').click()
  expect((await fixedResponse).ok()).toBeTruthy()
  const table = page.getByTestId('evaluation-run-table')
  await expect(table.locator('.el-table__body-wrapper tbody tr')).toHaveCount(10)
  const secondResponse = waitForQuery(page, '/evaluations', { ...fixedParams, page: 2 })
  await page.getByTestId('evaluation-run-pagination').locator('.el-pager').getByText('2', { exact: true }).click()
  expect((await secondResponse).ok()).toBeTruthy()
  const fixedSecond = await getData(page.request, '/evaluations', { ...fixedParams, page: 2 })
  await expect.poll(async () => table.locator('.el-table__body-wrapper tbody a').evaluateAll(links => links.map(link => link.getAttribute('href')))).toEqual(fixedSecond.items.map(item => '/evaluations/' + item.id))
  await page.getByTestId('evaluation-filter-mode').click()
  await page.getByRole('option', { name: 'Agent 扩展', exact: true }).click()
  const agentParams = { ...fixedParams, mode: 'dual_agent', page: 1 }
  const agentResponse = waitForQuery(page, '/evaluations', agentParams)
  await page.getByTestId('evaluation-filter-apply').click()
  expect((await agentResponse).ok()).toBeTruthy()
  const agents = await getData(page.request, '/evaluations', agentParams)
  expect(agents.total).toBeGreaterThanOrEqual(1)
  await expect.poll(async () => table.locator('.el-table__body-wrapper tbody a').evaluateAll(links => links.map(link => link.getAttribute('href')))).toEqual(agents.items.map(item => '/evaluations/' + item.id))
  await expect(page.getByTestId('evaluation-run-pagination').locator('.el-pager .is-active')).toHaveText('1')
  await expect(page.getByTestId('evaluation-question')).toHaveValue(creationQuestion)
  await expect(page.getByTestId('evaluation-company')).toContainText(fixture.stock_name)
  await expect(page.getByTestId('evaluation-mode').getByRole('radio', { name: '固定双源', exact: true })).toBeChecked()
  await page.screenshot({ path: '../test-results/browser-evaluation-query-page.png', fullPage: true })
})
