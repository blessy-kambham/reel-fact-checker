import React from 'react';

function Evidence({ item, demo }) {
  return <article className="evidence">
    <span className="evidence-label">{item.stance === 'FOR' ? 'Supporting evidence' : item.stance === 'AGAINST' ? 'Contradicting evidence' : 'Context'}</span>
    <p>{item.statement}</p>
    <blockquote>{item.quote}</blockquote>
    {item.url?.startsWith('https://') ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title} ↗</a> : <strong>{item.title}</strong>}
    <p className="verification">{demo ? 'Demo fixture' : item.verified ? 'Quote and attribution checked' : 'Unverified'} · {item.verification}</p>
  </article>;
}

export default function Report({ report }) {
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${report.mode}-report-${report.id}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="report" aria-label="Fact-check report">
    <div className="card-heading"><p className="eyebrow">{report.mode === 'demo' ? 'FICTIONAL DEMO REPORT' : 'RESEARCH REPORT'}</p><button className="small-button" onClick={download}>Download JSON</button></div>
    <h2>{report.mode === 'demo' ? 'See how evidence changes the story.' : 'Your claim-by-claim report'}</h2>
    <p className="notice">{report.note}</p>
    <p className="report-meta">{new Date(report.created_at).toLocaleString()} · {report.claims.length} claims · {report.usage.search_calls} searches · {report.usage.model_calls} model calls</p>
    {!report.claims.length && <p>No factual claims were researched. Classification: {report.intent}.</p>}
    {report.claims.map((claim, index) => <article className="claim-result" key={index}>
      <p className="eyebrow">CLAIM {index + 1} · {claim.status.toUpperCase()}</p>
      <h3>{claim.claim}</h3>
      <span className={`verdict verdict-${claim.verdict.toLowerCase().replaceAll(' ', '-')}`}>{report.mode === 'demo' ? 'EXAMPLE: ' : ''}{claim.verdict}</span>
      <p className="report-meta">{claim.sources_checked} pages retrieved · Confidence is not calibrated</p>
      <div className="evidence-grid">{claim.evidence.map((item, i) => <Evidence key={i} item={item} demo={report.mode === 'demo'} />)}</div>
      {!claim.evidence.length && <p>No verified evidence is available for this claim.</p>}
      <details><summary>Research coverage & limitations</summary><p><strong>Supporting search:</strong> {claim.supporting_search}</p><p><strong>Contradicting search:</strong> {claim.contradicting_search}</p><ul>{claim.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
    </article>)}
    <aside className="report-limitations"><h3>Keep in mind</h3><ul>{report.limitations.map((text,i) => <li key={i}>{text}</li>)}</ul><p>Reports remain in this tab until you clear or reload it. Download a copy to keep it.</p></aside>
  </section>;
}
