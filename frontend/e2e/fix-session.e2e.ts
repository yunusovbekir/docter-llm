import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'

type Schema = components['schemas']

const model = 'gpt-4.1-mini'
const message = 'Fix the installation instructions.'
const createdAt = '2026-01-01T00:00:00Z'
const file = 'README.md'
const branch = 'docsmith/fix-installation'
const original = 'Install with old-command.'
const proposed = 'Install with pnpm install.'

function mockApi(page: Page) {
  const settings: Schema['SettingsRead'] = {
    provider: 'openai',
    model: 'gpt-4.1',
    base_url: null,
    embedding_model: 'text-embedding-3-small',
    temperature: 0.3,
    max_agent_steps: 20,
    has_api_key: true,
  }
  const project: Schema['ProjectRead'] = {
    id: 8,
    name: 'docs',
    path: '/repos/docs',
    current_branch: 'main',
    index_status: 'idle',
    indexed_at: null,
    created_at: createdAt,
  }
  const session: Schema['SessionRead'] = {
    id: 17,
    project_id: project.id,
    mode: 'fix',
    mode_params: {},
    run_status: 'idle',
    branch_name: null,
    created_at: createdAt,
    updated_at: createdAt,
  }
  const patch = {
    file_path: file,
    patch: `--- a/${file}\n+++ b/${file}\n@@ -1 +1 @@\n-${original}\n+${proposed}\n`,
    rationale: 'Update the install command.',
  }
  const change: Schema['ChangeRead'] = {
    ...patch,
    id: 10,
    change_set_id: 1,
    status: 'pending',
    parent_change_id: null,
    created_at: createdAt,
    original_content: `${original}\n`,
    proposed_content: `${proposed}\n`,
    hunks: [{ id: 1, change_id: 10, index: 0, header: '@@ -1 +1 @@', status: 'pending' }],
    comments: [],
  }
  const changeSet: Schema['ChangeSetRead'] = {
    id: 1,
    session_id: session.id,
    run_id: 'fix-run',
    created_at: createdAt,
    changes: [change],
  }
  const events = [
    { type: 'token', text: 'I updated the ' },
    { type: 'token', text: '**installation instructions**.' },
    { type: 'tool_call', id: 'patch', name: 'propose_patch', arguments: patch },
    {
      type: 'tool_result',
      id: 'patch',
      name: 'propose_patch',
      structured: { ...patch, is_new_file: false },
      text: JSON.stringify({ ...patch, is_new_file: false }),
      is_error: false,
    },
    { type: 'change_proposed', change_id: change.id, change: { ...patch, is_new_file: false } },
    { type: 'done', reason: 'completed', steps: 2 },
  ]

  let projectAdded = false
  let sessionStarted = false
  let changeProposed = false
  let indexPolls = 0
  const projectUrl = `/api/projects/${project.id}`
  const sessionUrl = `/api/sessions/${session.id}`

  return page.route('**/api/**', async (route) => {
    const request = route.request()
    const endpoint = `${request.method()} ${new URL(request.url()).pathname}`
    switch (endpoint) {
      case 'GET /api/settings':
        return route.fulfill({ json: settings })
      case 'PUT /api/settings':
        expect(request.postDataJSON()).toEqual({ model })
        settings.model = model
        return route.fulfill({ json: settings })
      case 'GET /api/projects':
        return route.fulfill({ json: projectAdded ? [project] : [] })
      case 'POST /api/projects':
        expect(request.postDataJSON()).toEqual({ path: project.path })
        projectAdded = true
        return route.fulfill({ status: 201, json: { ...project, is_dirty: false, warnings: [] } })
      case `GET ${projectUrl}`:
        expect(projectAdded).toBe(true)
        return route.fulfill({ json: project })
      case `POST ${projectUrl}/index`:
        expect(project.index_status).toBe('idle')
        project.index_status = 'running'
        return route.fulfill({ status: 202, json: { status: 'running', progress: 0 } })
      case `GET ${projectUrl}/index/status`:
        if (project.index_status === 'running' && ++indexPolls === 2) {
          project.index_status = 'done'
          project.indexed_at = createdAt
        }
        return route.fulfill({
          json: {
            status: project.index_status,
            progress: project.index_status === 'done' ? 100 : indexPolls ? 50 : 0,
            indexed_at: project.indexed_at,
          } satisfies Schema['ProjectIndexStatus'],
        })
      case 'POST /api/sessions':
        expect(project.index_status).toBe('done')
        expect(request.postDataJSON()).toEqual({ project_id: project.id, mode: 'fix' })
        sessionStarted = true
        return route.fulfill({ status: 201, json: session })
      case `GET ${sessionUrl}`:
        expect(sessionStarted).toBe(true)
        return route.fulfill({ json: { ...session, messages: [] } })
      case `GET ${sessionUrl}/changes`:
        return route.fulfill({ json: changeProposed ? [changeSet] : [] })
      case `POST ${sessionUrl}/chat`:
        expect(request.postDataJSON()).toEqual({ message })
        changeProposed = true
        return route.fulfill({
          contentType: 'text/event-stream',
          body: events
            .map(
              (event, i) =>
                `id: fix-run:${i}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
            )
            .join(''),
        })
      case `POST /api/changes/${change.id}/approve`:
        expect(changeProposed).toBe(true)
        expect(request.postData()).toBeNull()
        change.status = 'applied'
        change.hunks[0]!.status = 'applied'
        session.branch_name = branch
        return route.fulfill({ json: change })
      default:
        await route.abort()
        throw new Error(`Unexpected API request: ${endpoint}`)
    }
  })
}

test('saves settings, indexes a project, and approves a Fix proposal', async ({ page }) => {
  await mockApi(page)

  await test.step('Save settings', async () => {
    await page.goto('/settings')
    await page.getByLabel('Model', { exact: true }).fill(model)
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(
      page.getByRole('button', { name: 'Dismiss notification: Settings saved.' }),
    ).toBeVisible()
  })

  await test.step('Add and index a project', async () => {
    await page.getByRole('link', { name: 'Projects', exact: true }).click()
    await page.getByRole('button', { name: 'Add project' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add project' })
    await dialog.getByLabel('Absolute repository path').fill('/repos/docs')
    await dialog.getByRole('button', { name: 'Add project' }).click()
    await page.getByRole('link', { name: 'docs', exact: true }).click()
    await expect(page).toHaveURL('/projects/8')
    await expect(page.getByRole('button', { name: 'Start session' })).toBeDisabled()
    await page.getByRole('button', { name: 'Index', exact: true }).click()
    await expect(page.getByText('Indexing complete', { exact: true })).toBeVisible()
  })

  await test.step('Start a Fix session and read the streamed reply and proposal', async () => {
    const fix = page.getByRole('radio', { name: 'Fix', exact: true })
    await fix.focus()
    await fix.press('Space')
    await expect(fix).toBeChecked()
    await page.getByRole('button', { name: 'Start session' }).click()
    await expect(page).toHaveURL('/sessions/17')
    await expect(page.getByText('No changes proposed yet.')).toBeVisible()
    await page.getByRole('textbox', { name: 'Message' }).fill(message)
    await page.getByRole('button', { name: 'Send', exact: true }).click()

    const chat = page.getByRole('log')
    await expect(chat.getByText('I updated the installation instructions.')).toBeVisible()
    await expect(chat.locator('summary').filter({ hasText: 'propose_patch' })).toContainText('OK')
    await expect(page.getByRole('button', { name: /README\.md.*Pending/ })).toBeVisible()
  })

  await test.step('Inspect the diff and approve the change', async () => {
    await page.getByRole('button', { name: /README\.md.*Pending/ }).click()
    const diff = page.getByRole('region', { name: `Diff: ${file}` })
    await expect(diff.getByText(original, { exact: true })).toBeVisible()
    await expect(diff.getByText(proposed, { exact: true })).toBeVisible()
    await expect(page.getByText(`Branch: ${branch}`, { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Approve', exact: true }).click()
    await expect(page.getByRole('button', { name: /README\.md.*Applied/ })).toBeVisible()
    await expect(page.getByText(`Branch: ${branch}`, { exact: true })).toBeVisible()
  })
})
