import React, { useEffect, useRef } from 'react';

function Evidence({ item, demo, cited }) {
  return <article className={`evidence evidence-${item.stance === 'FOR' ? 'for' : item.stance === 'AGAINST' ? 'against' : 'context'}`}>
    <span className="evidence-label">{item.evidence_id ? `${item.evidence_id} · ` : ''}{item.stance === 'FOR' ? 'Supporting evidence' : item.stance === 'AGAINST' ? 'Contradicting evidence' : 'Context'}</span>
    {cited && <span className="verdict-basis">Used for verdict</span>}
    <p>{item.statement}</p>
    <blockquote>{item.quote}</blockquote>
    {item.url?.startsWith('https://') ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a> : <strong>{item.title}</strong>}
    <p className="verification">{demo ? 'Demo fixture' : item.verified ? 'Quote and attribution checked' : 'Unverified'} · {item.verification}</p>
    {item.source_label && <p className={`verification source-type source-${item.source_tier}`}>Source type: {item.source_label}</p>}
    {item.retrieval === 'search_copy' && <p className="verification">Page text from the search provider's copy (the site refused a direct fetch).</p>}
  </article>;
}

// What the agents chose to do, in order. Each line is "Agent name: what it did".
function AgentSteps({ steps, label }) {
  if (!steps?.length) return null;
  return <details className="agent-steps"><summary>{label} ({steps.length} {steps.length === 1 ? 'step' : 'steps'})</summary>
    <ol>{steps.map((text, i) => {
      const at = text.indexOf(': ');
      return <li key={i}>{at > 0 ? <><strong>{text.slice(0, at)}</strong>{text.slice(at)}</> : text}</li>;
    })}</ol></details>;
}

export default function Report({ report }) {
  const reportElement = useRef(null);
  useEffect(() => {
    let closedDetails = [];
    function preparePrint() {
      // Browser menu printing should include the same disclosures as the button.
      closedDetails = [...reportElement.current.querySelectorAll('details:not([open])')];
      closedDetails.forEach(element => { element.open = true; });
      document.body.classList.add('printing-report');
    }
    function restoreScreen() {
      closedDetails.forEach(element => { element.open = false; });
      closedDetails = [];
      document.body.classList.remove('printing-report');
    }
    window.addEventListener('beforeprint', preparePrint);
    window.addEventListener('afterprint', restoreScreen);
    return () => {
      window.removeEventListener('beforeprint', preparePrint);
      window.removeEventListener('afterprint', restoreScreen);
      restoreScreen();
    };
  }, []);
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${report.mode}-report-${report.id}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section ref={reportElement} className="report" aria-label="Fact-check report">
    <div className="card-heading"><p className="report-kind">{report.mode === 'demo' ? 'Fictional demo report' : 'Research report'}</p><div className="report-actions"><button className="small-button" onClick={() => window.print()}>Print / Save PDF</button><button className="small-button" onClick={download}>Download JSON</button></div></div>
    <h2>{report.mode === 'demo' ? 'See how evidence changes the story.' : 'Your claim-by-claim report'}</h2>
    {report.input_type === 'article' && <p className="report-meta">Article: {report.source_url?.startsWith('https://') ? <a href={report.source_url} target="_blank" rel="noopener noreferrer">{report.source_url}</a> : report.source_url}</p>}
    {report.input_type === 'article' && report.coverage_status === 'incomplete' && <p className="notice" role="alert">Some selected claims were not copied word for word from the article, so they were not researched.</p>}
    {report.input_type === 'video' && report.media && <p className="report-meta">Video: {report.media.duration_seconds.toFixed(0)} s · {report.media.had_audio ? `speech transcribed${report.media.transcript_language ? ` (${report.media.transcript_language})` : ''}` : 'no audio'} · on-screen text read from {report.media.frames_read} frames</p>}
    {report.input_type === 'video' && report.coverage_status === 'incomplete' && <p className="notice" role="alert">Some selected claims were not found word for word in the video's transcript, on-screen text or caption, so they were not researched.</p>}
    {report.input_type === 'video' && report.source_text && <details className="video-content"><summary>What the video says (transcript, on-screen text, caption)</summary><p className="report-meta">Automatically transcribed and read; check it against the video.</p><pre>{report.source_text}</pre></details>}
    {!['article', 'video'].includes(report.input_type) && ['incomplete', 'unavailable'].includes(report.coverage_status) && <p className="notice" role="alert">Extraction coverage {report.coverage_status}. No research was started and no verdict was established. Submit each assertion separately.</p>}
    {report.omitted_claims && !report.coverage_status && <p className="notice" role="alert">Incomplete coverage: some assertions were omitted. Verdicts are withheld. Submit each assertion separately.</p>}
    {report.note && <p className="notice">{report.note}</p>}
    <p className="report-meta">{new Date(report.created_at).toLocaleString()}, {report.claims.length} {report.claims.length === 1 ? 'claim' : 'claims'} checked</p>
    <AgentSteps steps={report.agent_steps} label="How the agents prepared this report" />
    {!report.claims.length && <p>No factual claims were researched. Classification: {report.intent}.</p>}
    {report.claims.map((claim, index) => <article className="claim-result" key={index}>
      <p className="claim-number">Claim {index + 1} ({claim.status})</p>
      <h3>{claim.claim}</h3>
      <span className={`verdict verdict-${claim.verdict.toLowerCase().replaceAll(' ', '-')}`}>{report.mode === 'demo' ? 'Example: ' : ''}{claim.verdict}</span>
      {report.mode !== 'demo' && claim.verdict_state === 'withheld' && <p className="notice withheld" role="note"><strong>Verdict withheld.</strong> {claim.withheld_message || 'The verdict could not be established.'}</p>}
      {report.mode !== 'demo' && claim.verdict_state === 'issued' && claim.verdict !== 'UNVERIFIABLE' && claim.verdict_source_count === 1 && <p className="notice withheld" role="note"><strong>Single source.</strong> This verdict rests on one web page. Check it before relying on the verdict.</p>}
      {report.mode !== 'demo' && claim.evidence_strength && <p className={`source-strength strength-${claim.evidence_strength}`}>
        <strong>Source strength: {claim.evidence_strength}</strong> · source score {claim.source_score}/100
        <span> — rates where the cited pages come from, not how likely the verdict is to be right.</span></p>}
      <p className="report-meta">{claim.sources_checked} {claim.sources_checked === 1 ? 'page' : 'pages'} read</p>
      {(() => {
        const demo = report.mode === 'demo';
        const usedIds = !demo && claim.verdict_state === 'issued' ? claim.verdict_evidence_ids || [] : [];
        const used = claim.evidence.filter(item => usedIds.includes(item.evidence_id));
        const other = claim.evidence.filter(item => !usedIds.includes(item.evidence_id));
        if (!used.length) return <div className="evidence-grid">{claim.evidence.map((item, i) => <Evidence key={i} item={item} demo={demo} />)}</div>;
        return <>
          <p className="evidence-group">Evidence used for the verdict</p>
          <div className="evidence-grid">{used.map((item, i) => <Evidence key={i} item={item} demo={demo} cited />)}</div>
          {other.length > 0 && <details className="other-evidence">
            <summary>Other verified evidence ({other.length}) — not used for the verdict</summary>
            <p>These passages passed citation checks but the verdict did not rely on them. Some may concern a different assertion.</p>
            <div className="evidence-grid">{other.map((item, i) => <Evidence key={i} item={item} demo={demo} />)}</div>
          </details>}
        </>;
      })()}
      {!claim.evidence.length && <p>No verified evidence is available for this claim.</p>}
      {claim.rejected_citations?.length > 0 && <details className="rejected-citations">
        <summary>Excluded citations ({claim.rejected_citations.length}) — not evidence</summary>
        <p>These proposed citations failed validation and were not used to support the verdict. Their text may be inaccurate.</p>
        {claim.rejected_citations.map((item, i) => <article className="evidence" key={i}>
          <strong>{item.title}</strong>
          <p><strong>Why excluded:</strong> {item.verification}</p>
          <p className="verification">Check: {item.verification_code?.replaceAll('_', ' ') || 'Unverified'}</p>
          <details><summary>Inspect the unverified proposal</summary>
            <p><strong>Unverified statement:</strong> {item.statement}</p>
            <blockquote>{item.quote}</blockquote>
            {item.url?.startsWith('https://') && <a href={item.url} target="_blank" rel="noopener noreferrer">Inspect source</a>}
            {item.retrieved_at && <p className="verification">Retrieved: {new Date(item.retrieved_at).toLocaleString()}</p>}
          </details>
        </article>)}
      </details>}
      <AgentSteps steps={claim.agent_steps} label="How the agents worked on this claim" />
      <details><summary>Research coverage & limitations</summary><p><strong>Supporting search:</strong> {claim.supporting_search}</p><p><strong>Contradicting search:</strong> {claim.contradicting_search}</p><ul>{claim.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
    </article>)}
    <aside className="report-limitations"><h3>Keep in mind</h3><ul>{report.limitations.map((text,i) => <li key={i}>{text}</li>)}</ul>{report.mode === 'live' && <p className="screen-only">This report is also kept under Saved reports.</p>}</aside>
  </section>;
}
