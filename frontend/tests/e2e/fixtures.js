// Mocked backend for browser tests. The app's default API base is http://127.0.0.1:8000.
export const API = 'http://127.0.0.1:8000';

export const liveConfig = {
  live_ready: true, live_enabled: true, missing_settings: [], max_claims: 3, message: 'Live research is ready.',
  video_ready: true, video_message: 'Video checks are available.',
  spending: { spent_usd: 0.031, limit_usd: 0.5, searches: 4, search_limit: 40, stopped: false },
};
export const offlineConfig = { ...liveConfig, live_ready: false, live_enabled: false, spending: null,
  message: 'Free demo is available. Live research is not configured or enabled.' };

const citation = (id, stance, statement, url) => ({
  source_id: 'S1', quote: `Quoted passage for ${id}.`, statement, stance, title: `Source ${id}`, url,
  verified: true, verification: 'Quote and attribution checked.', verification_code: 'verified', evidence_id: id,
  excerpt_id: 'S1:E1', source_start: 0, source_end: 10, proposed_stance: stance, relation_reason: 'Scripted',
  retrieved_at: '2026-09-30T12:00:00+00:00', source_text_sha256: 'a'.repeat(64),
});

export function report(overrides = {}, claimOverrides = {}) {
  return {
    id: '11111111-1111-4111-8111-111111111111', mode: 'live', submitted_text: 'The fictional tower is 300 metres tall.',
    created_at: '2026-09-30T12:00:00+00:00', intent: 'FACTUAL', note: '', limitations: ['Local MVP limitation.'],
    usage: { model_calls: 6, search_calls: 2, input_tokens: 1, output_tokens: 1 }, omitted_claims: false,
    input_spans: [], coverage_status: 'passed', input_type: 'text', source_url: null, source_sha256: null,
    claims: [{
      claim: 'The fictional tower is 300 metres tall.', verdict: 'TRUE', status: 'complete',
      evidence: [citation('E1', 'FOR', 'The tower measures 300 metres.', 'https://a.example.org/'),
                 citation('E2', 'FOR', 'A neighbouring bridge opened in 1990.', 'https://b.example.org/')],
      rejected_citations: [], limitations: ['Claim limitation.'], supporting_search: 'done', contradicting_search: 'done',
      sources_checked: 2, verdict_state: 'issued', withheld_reason: null, withheld_message: null,
      decision_verdict: 'TRUE', decision_evidence_ids: ['E1'], verdict_evidence_ids: ['E1'], verdict_source_count: 1,
      ...claimOverrides,
    }],
    ...overrides,
  };
}

export async function mockBackend(page, { config = liveConfig, factCheck, article, video, login, history = [], saved = {} } = {}) {
  const calls = [];
  await page.route(`${API}/**`, async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    calls.push({ method: request.method(), path, body: request.postData() });
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/config') return config === 'down' ? route.abort() : json(200, typeof config === 'function' ? config() : config);
    if (path === '/login' && login) return login(route, json, request);
    if (path === '/logout') return json(200, { signed_in: false });
    if (path === '/demo') return json(200, report({ mode: 'demo', id: '22222222-2222-4222-8222-222222222222' }));
    if (path === '/fact-check') return factCheck ? factCheck(route, json) : json(200, report());
    if (path === '/fact-check-article') return article ? article(route, json) : json(200, report());
    if (path === '/fact-check-video') return video ? video(route, json) : json(200, report());
    if (path === '/history') return json(200, { reports: history });
    const match = path.match(/^\/history\/(.+)$/);
    if (match && request.method() === 'DELETE') return json(200, { deleted: match[1] });
    if (match) return saved[match[1]] ? json(200, saved[match[1]]) : json(404, { detail: 'Report not found.' });
    return json(404, { detail: 'Not mocked' });
  });
  return calls;
}
