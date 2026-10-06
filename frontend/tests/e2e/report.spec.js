import { test, expect } from '@playwright/test';
import { liveConfig, mockBackend, offlineConfig, report } from './fixtures.js';

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
  await expect(page.getByText(/OpenAI|Tavily/)).toHaveCount(0);
  await page.getByText('Status', { exact: true }).click();
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
  await expect(page.getByRole('alert')).toContainText('Could not reach the server');
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

test('video upload goes to the video route and shows what the video says', async ({ page }) => {
  const calls = await mockBackend(page, { video: (route, json) => json(200, report({
    input_type: 'video', submitted_text: 'Video: reel.mp4', coverage_status: 'passed',
    source_text: 'What is said:\nThe fictional lake never freezes.\n\nText on screen:\nNEVER FREEZES',
    media: { duration_seconds: 42, had_audio: true, transcript_language: 'en', frames_read: 4,
             transcript_chars: 33, screen_text_chars: 13, caption_chars: 0 } })) });
  await page.goto('/');
  await page.getByRole('button', { name: 'Video', exact: true }).click();
  await page.getByLabel('Video file').setInputFiles({ name: 'reel.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fake video') });
  await page.getByLabel('Caption (optional)').fill('Did you know?');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText(/Video: 42 s · speech transcribed \(en\)/)).toBeVisible();
  await page.getByText(/What the video says/).click();
  await expect(page.getByText('NEVER FREEZES')).toBeVisible();
  const post = calls.find(c => c.path === '/fact-check-video');
  expect(post.method).toBe('POST');
  expect(post.body).toContain('reel.mp4');
  expect(post.body).toContain('Did you know?');
});

test('a video link goes to the link route and the report shows where it came from', async ({ page }) => {
  const link = 'https://www.instagram.com/reel/abc123/';
  const calls = await mockBackend(page, { videoLink: (route, json) => json(200, report({
    input_type: 'video', submitted_text: `Video link: ${link}`, source_url: link, coverage_status: 'passed' })) });
  await page.goto('/');
  await page.getByRole('button', { name: 'Video', exact: true }).click();
  await page.getByRole('button', { name: 'Paste a link' }).click();
  await expect(page.getByText('One public video on Instagram, TikTok, YouTube, up to 3 minutes')).toBeVisible();
  await expect(page.getByText(/Only use links to videos you are allowed to download/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Research this claim/ })).toBeDisabled();
  await page.getByLabel('Video link').fill(link);
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Your claim-by-claim report')).toBeVisible();
  await expect(page.getByRole('link', { name: link })).toBeVisible();
  const post = calls.find(c => c.path === '/fact-check-video-link');
  expect(JSON.parse(post.body)).toEqual({ url: link, caption: '' });
  expect(calls.some(c => c.path === '/fact-check-video')).toBe(false);
});

test('video links say when they are turned off and cannot be submitted', async ({ page }) => {
  await mockBackend(page, { config: { ...liveConfig, video_link_ready: false,
    video_link_message: 'Checking a video from a link is turned off on this copy. Upload the video file instead.' } });
  await page.goto('/');
  await page.getByRole('button', { name: 'Video', exact: true }).click();
  await page.getByRole('button', { name: 'Paste a link' }).click();
  await page.getByLabel('Video link').fill('https://www.instagram.com/reel/abc123/');
  await expect(page.getByText(/turned off on this copy/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Research this claim/ })).toBeDisabled();
  // Uploading still works.
  await page.getByRole('button', { name: 'Upload a file' }).click();
  await expect(page.getByLabel('Video file')).toBeVisible();
});

test('video mode explains missing tools and cannot be submitted', async ({ page }) => {
  await mockBackend(page, { config: { ...liveConfig, video_ready: false,
    video_message: 'Video checks need ffmpeg. On a Mac: brew install ffmpeg, then restart the backend.' } });
  await page.goto('/');
  await page.getByRole('button', { name: 'Video', exact: true }).click();
  await page.getByLabel('Video file').setInputFiles({ name: 'reel.mp4', mimeType: 'video/mp4', buffer: Buffer.from('x') });
  await expect(page.getByText(/brew install ffmpeg/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Research this claim/ })).toBeDisabled();
});

test('a private site asks for the password before showing research', async ({ page }) => {
  let signedIn = false;
  await mockBackend(page, {
    config: () => ({ ...liveConfig, auth: { required: true, signed_in: signedIn } }),
    login: (route, json, request) => {
      if (JSON.parse(request.postData()).password !== 'right password') return json(401, { detail: 'Incorrect password.' });
      signedIn = true;
      return json(200, { signed_in: true });
    },
  });
  await page.goto('/');
  await expect(page.getByLabel('Your statement')).toHaveCount(0);
  await expect(page.getByText('Saved reports')).toHaveCount(0);
  await page.getByLabel(/access password/).fill('wrong');
  await page.getByRole('button', { name: /Sign in/ }).click();
  await expect(page.getByRole('alert')).toContainText('Incorrect password.');
  await page.getByLabel(/access password/).fill('right password');
  await page.getByRole('button', { name: /Sign in/ }).click();
  await expect(page.getByLabel('Your statement')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
});

test('the steps the agents chose are listed with the claim and the report', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(200, report(
    { agent_steps: ['Claim Extractor: Revised its extraction after the coverage check found a problem; the revision passed the check.'] },
    { agent_steps: ['Orchestrator: Sent the claim to the Research Agent.',
      'Research Agent: Searched for supporting evidence: "tower height official" (3 new result(s)).',
      'Orchestrator: Asked the Verdict Agent for a verdict.'] })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await page.getByText('How the agents worked on this claim (3 steps)').click();
  await expect(page.getByText(/tower height official/)).toBeVisible();
  await page.getByText('How the agents prepared this report (1 step)').click();
  await expect(page.getByText(/Revised its extraction/)).toBeVisible();
});

test('evidence taken from the search provider copy is labelled', async ({ page }) => {
  const [first, second] = report().claims[0].evidence;
  await mockBackend(page, { factCheck: (route, json) => json(200, report({}, {
    evidence: [{ ...first, retrieval: 'search_copy' }, { ...second, retrieval: 'fetched' }] })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText(/search provider's copy/)).toHaveCount(1);
});

test('source types and source strength are shown for an issued verdict', async ({ page }) => {
  const [first, second] = report().claims[0].evidence;
  await mockBackend(page, { factCheck: (route, json) => json(200, report({}, {
    evidence_strength: 'moderate', source_score: 75, verdict_site_count: 2,
    evidence: [{ ...first, source_tier: 'official', source_label: 'Official, academic or peer-reviewed source' },
               { ...second, source_tier: 'unrated', source_label: 'Unrated website' }] })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Source strength: moderate')).toBeVisible();
  await expect(page.getByText(/source score 75\/100/)).toBeVisible();
  await expect(page.getByText(/2 different sites cited \(goal: 3\)/)).toBeVisible();
  await expect(page.getByText('Source type: Official, academic or peer-reviewed source')).toBeVisible();
});

test('overall verdict, confidence, reuse and source weights are shown', async ({ page }) => {
  const [first, second] = report().claims[0].evidence;
  await mockBackend(page, { factCheck: (route, json) => json(200, report(
    { overall_verdict: 'PARTIALLY TRUE', overall_summary: 'Of 2 claims: 1 true, 1 false.' },
    { confidence: 'medium', confidence_reasons: ['it rests on 2 sites, fewer than 3', 'two or more of them are official, academic or established sources'],
      reused_from: 'earlier-report', first_checked_at: '2026-10-05T09:30:00+00:00',
      explanation: 'The tower measures 300 metres [E1].',
      evidence_balance: { supporting: { sites: 2, weight: 1.45 }, contradicting: { sites: 1, weight: 0.5 } },
      evidence: [{ ...first, source_tier: 'official', source_label: 'Government or intergovernmental body', source_weight: 0.95 }, second] })) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.locator('.overall-verdict')).toContainText('PARTIALLY TRUE');
  await expect(page.getByText('Of 2 claims: 1 true, 1 false.')).toBeVisible();
  await expect(page.getByText('Confidence: medium')).toBeVisible();
  await expect(page.getByText(/it rests on 2 sites, fewer than 3; two or more of them/)).toBeVisible();
  await expect(page.getByText(/A rule based on the evidence, not a measured probability/)).toBeVisible();
  await expect(page.getByText('Checked before.')).toBeVisible();
  await expect(page.locator('.explanation')).toHaveText('The tower measures 300 metres [E1].');
  await expect(page.getByText('Supporting: 2 sites, weight 1.45')).toBeVisible();
  await expect(page.getByText('Contradicting: 1 site, weight 0.50')).toBeVisible();
  await expect(page.locator('.balance-for')).toHaveAttribute('width', '74');
  await expect(page.getByText(/Government or intergovernmental body · credibility weight 0\.95/)).toBeVisible();
});

test('reports without the newer fields show none of those lines', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(200, report()) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Your claim-by-claim report')).toBeVisible();
  await expect(page.locator('.overall-verdict')).toHaveCount(0);
  await expect(page.getByText(/Confidence:/)).toHaveCount(0);
  await expect(page.getByText('Checked before.')).toHaveCount(0);
  await expect(page.getByText(/credibility weight/)).toHaveCount(0);
  await expect(page.locator('.explanation')).toHaveCount(0);
  await expect(page.locator('.evidence-balance')).toHaveCount(0);
});

test('reports without source ratings show no strength line', async ({ page }) => {
  await mockBackend(page, { factCheck: (route, json) => json(200, report()) });
  await page.goto('/');
  await page.getByLabel('Your statement').fill('Claim');
  await page.getByRole('button', { name: /Research this claim/ }).click();
  await expect(page.getByText('Your claim-by-claim report')).toBeVisible();
  await expect(page.getByText(/Source strength/)).toHaveCount(0);
  await expect(page.getByText(/Source type:/)).toHaveCount(0);
});
