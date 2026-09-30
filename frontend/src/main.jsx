import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Report from './Report';
import History from './History';
import './style.css';

const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

function App() {
  const [claim, setClaim] = useState('');
  const [inputType, setInputType] = useState('text');
  const [articleUrl, setArticleUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [config, setConfig] = useState(null);
  const [checking, setChecking] = useState(false);

  async function checkConfig() {
    setChecking(true);
    try {
      const response = await fetch(`${apiBase}/config`, { signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error();
      setConfig(await response.json());
      setError('');
    } catch {
      setConfig(null);
      setError('Could not reach the backend. Start FastAPI on port 8000, then retry the connection.');
    } finally { setChecking(false); }
  }
  useEffect(() => { checkConfig(); }, []);
  async function refreshSpending() {
    // Quietly update today's spending after a live report; never clears or replaces an error.
    try {
      const response = await fetch(`${apiBase}/config`, { signal: AbortSignal.timeout(8000) });
      if (response.ok) setConfig(await response.json());
    } catch { /* keep the previous status */ }
  }

  async function run(demo = false) {
    if (loading) return;
    setLoading(true); setReport(null); setError('');
    try {
      const article = !demo && inputType === 'article';
      const response = await fetch(`${apiBase}/${demo ? 'demo' : article ? 'fact-check-article' : 'fact-check'}`, {
        method: demo ? 'GET' : 'POST',
        ...(!demo && {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(article ? {url: articleUrl.trim()} : {claim: claim.trim()})}),
        signal: AbortSignal.timeout(demo ? 10000 : 345000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : article ? 'Please enter a public https:// article link.' : 'Please enter a claim between 1 and 5,000 characters.');
      if (!Array.isArray(data.claims) || !['live', 'demo'].includes(data.mode)) throw new Error('Unexpected backend response. Restart the backend and retry.');
      setReport(data);
    } catch (err) {
      setError(err.name === 'TimeoutError' ? 'The request timed out. Try again with a shorter claim.' : err instanceof TypeError ? 'The backend connection failed. Check the server and try again.' : err.message);
    } finally { setLoading(false); if (!demo) refreshSpending(); }
  }
  return <main>
    <header><a href="/" className="brand"><span className="mark">r.</span> reel fact-checker</a><span className="badge">{config?.live_ready ? 'LIVE RESEARCH ENABLED' : 'FREE LOCAL DEMO'}</span></header>
    <section className="intro"><p className="eyebrow">FROM CLAIMS TO CLARITY</p><h1>Pause the scroll.<br /><span>Question the claim.</span></h1><p className="lead">Follow the evidence, compare the context,<br />and see what still needs an answer.</p></section>
    <section className="card">
      <div className="card-heading"><h2>Check a claim</h2><span className="step">TEXT → EVIDENCE → REPORT</span></div>
      {!config?.live_ready && <aside className="setup-note"><strong>Start here. No accounts needed.</strong><p>Try a fictional example to explore the report. Real research stays off until you configure API access.</p><button className="demo-button" disabled={loading} onClick={() => run(true)}>Explore the free demo <span aria-hidden="true">↗</span></button></aside>}
      <div className="input-switch" role="group" aria-label="Input type">
        <button type="button" className="small-button" aria-pressed={inputType === 'text'} disabled={loading} onClick={() => setInputType('text')}>Statement</button>
        <button type="button" className="small-button" aria-pressed={inputType === 'article'} disabled={loading} onClick={() => setInputType('article')}>Article link</button>
      </div>
      <form onSubmit={event => { event.preventDefault(); run(); }}>
        {inputType === 'text' ? <>
          <label htmlFor="claim">Your statement</label>
          <textarea id="claim" value={claim} maxLength={5000} required disabled={loading} rows={4} placeholder="Paste a factual statement you want to investigate…" aria-describedby="claim-help" onChange={event => setClaim(event.target.value)} />
          <div className="input-meta" id="claim-help"><span>Text input · Up to 3 claims per report</span><span>{claim.length.toLocaleString()} / 5,000</span></div>
        </> : <>
          <label htmlFor="article-url">Article link</label>
          <input id="article-url" type="url" value={articleUrl} maxLength={2000} required disabled={loading} placeholder="https://…" aria-describedby="article-help" onChange={event => setArticleUrl(event.target.value)} />
          <div className="input-meta" id="article-help"><span>Public https:// news or blog page · Up to 3 central claims are checked</span></div>
        </>}
        <button disabled={loading || !(inputType === 'text' ? claim.trim() : articleUrl.trim()) || !config?.live_ready} type="submit">{loading ? 'Working…' : config?.live_ready ? 'Research this claim' : 'Live research needs API setup'} <span aria-hidden="true">↗</span></button>
      </form>
      {config?.live_ready && <p className="notice">Research sends text to OpenAI and queries to Tavily and may incur provider charges. Reports are experimental; inspect the evidence before relying on a verdict.</p>}
      {config?.live_ready && config?.spending && <p className="report-meta">Today (UTC): about ${config.spending.spent_usd.toFixed(3)} of ${config.spending.limit_usd.toFixed(2)} estimated OpenAI cost · {config.spending.searches} of {config.spending.search_limit} searches{config.spending.stopped ? ' · daily limit reached' : ''}</p>}
      <details className="setup-details"><summary>Connection & API setup</summary><p>{config?.message || 'Backend connection unavailable.'}</p><p>The free demo requires no keys. When you are ready, configure backend/.env using .env.example, then restart FastAPI. Keep keys out of this page and out of chat.</p>{config?.missing_settings?.length > 0 && <p>Missing settings: {config.missing_settings.join(', ')}</p>}<button type="button" className="small-button" disabled={checking || loading} onClick={checkConfig}>{checking ? 'Checking…' : 'Retry connection'}</button></details>
      {error && <div className="error" role="alert">{error}</div>}
      {loading && <p role="status" className="notice">Extracting claims, researching evidence, and checking citations. Live research can take several minutes.</p>}
      {config && <History apiBase={apiBase} disabled={loading} onOpen={data => { setError(''); setReport(data); }} />}
    </section>
    <div aria-live="polite">{report && <Report report={report} />}</div>
    <footer><span>Evidence first. Uncertainty made clear.</span><span>Text and article links today. Video comes later.</span></footer>
  </main>;
}
createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
