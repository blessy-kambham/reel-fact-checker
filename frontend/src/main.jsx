import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Report from './Report';
import History from './History';
import '@fontsource-variable/fraunces/full.css';
import '@fontsource-variable/figtree';
import './style.css';

// Unset: local development API. Empty string (production build): same origin as the page.
const apiBase = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

function App() {
  const [claim, setClaim] = useState('');
  const [inputType, setInputType] = useState('text');
  const [articleUrl, setArticleUrl] = useState('');
  const [videoFile, setVideoFile] = useState(null);
  const [caption, setCaption] = useState('');
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [config, setConfig] = useState(null);
  const [checking, setChecking] = useState(false);
  const [passwordInput, setPasswordInput] = useState('');
  const [signingIn, setSigningIn] = useState(false);

  async function checkConfig() {
    setChecking(true);
    try {
      const response = await fetch(`${apiBase}/config`, { signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error();
      setConfig(await response.json());
      setError('');
    } catch {
      setConfig(null);
      setError('Could not reach the server. Try again in a moment.');
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

  async function signIn(event) {
    event.preventDefault();
    setSigningIn(true); setError('');
    try {
      const response = await fetch(`${apiBase}/login`, { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ password: passwordInput }), signal: AbortSignal.timeout(10000) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Sign-in failed.');
      setPasswordInput('');
      await refreshSpending();
    } catch (err) { setError(err instanceof TypeError ? 'The connection failed. Try again in a moment.' : err.message); }
    finally { setSigningIn(false); }
  }

  async function signOut() {
    try { await fetch(`${apiBase}/logout`, { method: 'POST', signal: AbortSignal.timeout(10000) }); } catch { /* ignore */ }
    setReport(null);
    await refreshSpending();
  }

  const locked = Boolean(config?.auth?.required && !config?.auth?.signed_in);

  async function run(demo = false) {
    if (loading) return;
    setLoading(true); setReport(null); setError('');
    try {
      const article = !demo && inputType === 'article';
      const video = !demo && inputType === 'video';
      let body;
      if (video) { body = new FormData(); body.append('file', videoFile); body.append('caption', caption.trim()); }
      const response = await fetch(`${apiBase}/${demo ? 'demo' : video ? 'fact-check-video' : article ? 'fact-check-article' : 'fact-check'}`, {
        method: demo ? 'GET' : 'POST',
        ...(video && { body }),
        ...(!demo && !video && {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(article ? {url: articleUrl.trim()} : {claim: claim.trim()})}),
        signal: AbortSignal.timeout(demo ? 10000 : video ? 615000 : 345000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : video ? 'Please choose an MP4, MOV or WebM video and a caption under 2,200 characters.' : article ? 'Please enter a public https:// article link.' : 'Please enter a claim between 1 and 5,000 characters.');
      if (!Array.isArray(data.claims) || !['live', 'demo'].includes(data.mode)) throw new Error('Unexpected response from the server. Try again.');
      setReport(data);
    } catch (err) {
      setError(err.name === 'TimeoutError' ? 'The request timed out. Try again with a shorter claim.' : err instanceof TypeError ? 'The connection failed. Try again in a moment.' : err.message);
    } finally { setLoading(false); if (!demo) refreshSpending(); }
  }
  return <main>
    <header><a href="/" className="brand"><span className="mark">r.</span> reel fact-checker</a><span className={`badge${config?.live_ready ? ' is-live' : ''}`}>{config?.live_ready ? 'Live research on' : 'Free local demo'}</span></header>
    <section className="intro">
      <div><h1>Pause the scroll.<br />Question the claim.</h1><p className="lead">Paste a claim, an article link or a Reel. Get a verdict with the evidence behind it.</p></div>
      <div className="stickers" aria-hidden="true"><span className="sticker sticker-true">True</span><span className="sticker sticker-misleading">Misleading</span><span className="sticker sticker-false">False</span></div>
    </section>
    <section className="card">
      <div className="card-heading"><h2>Check a claim</h2></div>
      {!config?.live_ready && <aside className="setup-note"><strong>Try the demo.</strong><p>Explore a fictional example report. Live research is switched off on this copy.</p><button className="demo-button" disabled={loading} onClick={() => run(true)}>Explore the free demo</button></aside>}
      {locked && <form className="sign-in" onSubmit={signIn}>
        <label htmlFor="app-password">This site is private. Enter the access password to use live research.</label>
        <input id="app-password" type="password" autoComplete="current-password" value={passwordInput} maxLength={200} required disabled={signingIn} onChange={event => setPasswordInput(event.target.value)} />
        <button type="submit" disabled={signingIn || !passwordInput}>{signingIn ? 'Signing in…' : 'Sign in'}</button>
      </form>}
      {config?.auth?.required && config?.auth?.signed_in && <p className="report-meta">Signed in. <button type="button" className="link-button" onClick={signOut}>Sign out</button></p>}
      {!locked && <>
      <div className="input-switch" role="group" aria-label="Input type">
        <button type="button" className="small-button" aria-pressed={inputType === 'text'} disabled={loading} onClick={() => setInputType('text')}>Statement</button>
        <button type="button" className="small-button" aria-pressed={inputType === 'article'} disabled={loading} onClick={() => setInputType('article')}>Article link</button>
        <button type="button" className="small-button" aria-pressed={inputType === 'video'} disabled={loading} onClick={() => setInputType('video')}>Video</button>
      </div>
      <form onSubmit={event => { event.preventDefault(); run(); }}>
        {inputType === 'text' ? <>
          <label htmlFor="claim">Your statement</label>
          <textarea id="claim" value={claim} maxLength={5000} required disabled={loading} rows={4} placeholder="Paste a factual statement you want to investigate…" aria-describedby="claim-help" onChange={event => setClaim(event.target.value)} />
          <div className="input-meta" id="claim-help"><span>Up to {config?.max_claims || 5} claims per report</span><span>{claim.length.toLocaleString()} / 5,000</span></div>
        </> : inputType === 'video' ? <>
          <label htmlFor="video-file">Video file</label>
          <input id="video-file" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm" required disabled={loading} aria-describedby="video-help" onChange={event => setVideoFile(event.target.files?.[0] || null)} />
          <label htmlFor="video-caption">Caption (optional)</label>
          <textarea id="video-caption" value={caption} maxLength={2200} disabled={loading} rows={2} placeholder="Paste the post's caption if it makes claims…" onChange={event => setCaption(event.target.value)} />
          <div className="input-meta" id="video-help"><span>MP4, MOV or WebM, up to 3 minutes and 100 MB</span><span>{caption.length.toLocaleString()} / 2,200</span></div>
          {config && !config.video_ready && <p className="notice" role="note">{config.video_message}</p>}
        </> : <>
          <label htmlFor="article-url">Article link</label>
          <input id="article-url" type="url" value={articleUrl} maxLength={2000} required disabled={loading} placeholder="https://…" aria-describedby="article-help" onChange={event => setArticleUrl(event.target.value)} />
          <div className="input-meta" id="article-help"><span>A public news or blog page. Up to 3 central claims are checked.</span></div>
        </>}
        <button disabled={loading || !(inputType === 'text' ? claim.trim() : inputType === 'video' ? videoFile && config?.video_ready : articleUrl.trim()) || !config?.live_ready} type="submit">{loading ? 'Working…' : config?.live_ready ? 'Research this claim' : 'Live research needs API setup'}</button>
      </form>
      {config?.live_ready && <p className="notice">Verdicts are automated. Read the evidence before relying on one.</p>}
      {config?.spending?.stopped && <p className="notice withheld" role="note">Today's research limit has been reached. It resets at midnight UTC.</p>}
      <details className="setup-details"><summary>Status</summary><p>{config?.message || 'Server connection unavailable.'}</p>
        {config?.live_ready && config?.spending && <p>Today's usage: about ${config.spending.spent_usd.toFixed(3)} of ${config.spending.limit_usd.toFixed(2)}, {config.spending.searches} of {config.spending.search_limit} searches.</p>}
        {config?.missing_settings?.length > 0 && <p>Missing settings: {config.missing_settings.join(', ')}</p>}
        <button type="button" className="small-button" disabled={checking || loading} onClick={checkConfig}>{checking ? 'Checking…' : 'Retry connection'}</button></details>
      </>}
      {error && <div className="error" role="alert">{error}</div>}
      {loading && <p role="status" className="notice">Researching the claim and checking citations. This can take a minute or two.</p>}
      {config && !locked && <History apiBase={apiBase} disabled={loading} onOpen={data => { setError(''); setReport(data); }} />}
    </section>
    <div aria-live="polite">{report && <Report report={report} />}</div>
    <footer><span>Evidence first. Uncertainty made clear.</span></footer>
  </main>;
}
createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
