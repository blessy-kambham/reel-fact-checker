import { test, expect } from '@playwright/test';
import { mockBackend, offlineConfig, report } from './fixtures.js';

test('free demo works without live research and downloads JSON', async ({ page }) => {
  await mockBackend(page, { config: offlineConfig });
  await page.goto('/');
  await expect(page.getByText('FREE LOCAL DEMO')).toBeVisible();
  await expect(page.getByRole('button', { name: /Live research needs API setup/ })).toBeDisabled();
  await page.getByRole('button', { name: /Explore the free demo/ }).click();
  await expect(page.getByText('FICTIONAL DEMO REPORT')).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download JSON' }).click()]);
  expect(download.suggestedFilename()).toMatch(/^demo-report-.+\.json$/);
});

test('live report separates verdict evidence and flags a single source', async ({ page }) => {
  const calls = await mockBackend(page);
  await page.goto('/');
  await expect(page.getByText(/about \$0\.031 of \$0\.50/)).toBeVisible();
  await page.getByLabel('Your statement').fill('The fictional tower is 300 metres tall.');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Evidence used for the verdict')).toBeVisible();
  await expect(page.getByText('Used for verdict')).toHaveCount(1);
  await expect(page.getByText(/Single source\./)).toBeVisible();
  await expect(page.getByText(/Other verified evidence \(1\) — not used for the verdict/)).toBeVisible();
  const post = calls.find(c => c.path === '/fact-check');
  expect(JSON.parse(post.body)).toEqual({ claim: 'The fictional tower is 300 metres tall.' });
});

test('withheld verdicts show their reason', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(200, report({}, {
    verdict: 'UNVERIFIABLE', status: 'incomplete', verdict_state: 'withheld', withheld_reason: 'citation_failed',
    withheld_message: 'At least one proposed citation failed validation, so the verdict was withheld.',
    verdict_evidence_ids: [], verdict_source_count: null })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Verdict withheld.')).toBeVisible();
  await expect(page.getByText(/proposed citation failed validation/)).toBeVisible();
  await expect(page.getByText('Evidence used for the verdict')).toHaveCount(0);
});

test('incomplete text coverage explains that no research ran', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(200, report({ coverage_status: 'incomplete' })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText(/Extraction coverage incomplete\. No research was started/)).toBeVisible();
});

test('loading state is shown while a report runs', async ({ page }) => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  await mockBackend(page, { factCheck: async (route, json) => { await waiting; return json(200, report()); } });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByRole('button', { name: /Working…/ })).toBeDisabled();
  await expect(page.getByRole('status')).toContainText('checking citations');
  release();
  await expect(page.getByText('Your claim-by-claim report')).toBeVisible();
});

test('backend errors are shown to the user', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(429, { detail: "Today's spending limit has been reached." }) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByRole('alert')).toContainText("Today's spending limit has been reached.");
});

test('an unreachable backend is explained', async ({ page }) => {
  await mockBackend(page, { config: 'down' });
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Could not reach the backend');
});

test('article links go to the article route and show the source', async ({ page }) => {
  const url = 'https://news.example.org/story';
  const calls = await mockBackend(page, { article: (route, json) => json(200, report({
    input_type: 'article', source_url: url, submitted_text: url, coverage_status: 'incomplete' })) });
  await page.goto('/');
  await page.getByRole('button', { name: 'Article link' }).click();
  await page.getByLabel('Article link').fill(url);
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByRole('link', { name: url })).toBeVisible();
  await expect(page.getByText(/not copied word for word from the article/)).toBeVisible();
  await expect(page.getByText(/No research was started/)).toHaveCount(0);
  expect(JSON.parse(calls.find(c => c.path === '/fact-check-article').body)).toEqual({ url });
});

test('saved reports can be opened and deleted', async ({ page }) => {
  const saved = report({ id: '33333333-3333-4333-8333-333333333333', submitted_text: 'A saved claim' });
  const item = { id: saved.id, created_at: saved.created_at, intent: 'FACTUAL', coverage_status: 'passed',
    preview: 'A saved claim', claims: [{ claim: 'A saved claim', verdict: 'TRUE', verdict_state: 'issued' }] };
  const calls = await mockBackend(page, { history: [item], saved: { [saved.id]: saved } });
  await page.goto('/');
  await page.getByText('Saved reports').click();
  await expect(page.getByText('A saved claim')).toBeVisible();
  await page.getByRole('button', { name: 'Open' }).click();
  await expect(page.getByText('Your claim-by-claim report')).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Delete' }).click();
  await expect(page.locator('.history-item')).toHaveCount(0);
  await expect(page.getByText('No saved reports yet.')).toBeVisible();
  expect(calls.some(c => c.method === 'DELETE' && c.path === `/history/${saved.id}`)).toBe(true);
});

test('printing opens every collapsed section and restores it afterwards', async ({ page }) => {
  await mockBackend(page);
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  const details = page.locator('.report details');
  await expect(details.first()).toBeAttached();
  const total = await details.count();
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
  await expect(page.locator('.report details[open]')).toHaveCount(total);
  await expect(page.locator('body')).toHaveClass(/printing-report/);
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
  await expect(page.locator('.report details[open]')).toHaveCount(0);
});
