import React, { useEffect, useRef } from 'react';

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
    <div className="card-heading"><p className="eyebrow">{report.mode === 'demo' ? 'FICTIONAL DEMO REPORT' : 'RESEARCH REPORT'}</p><div className="report-actions"><button className="small-button" onClick={() => window.print()}>Print / Save PDF</button><button className="small-button" onClick={download}>Download JSON</button></div></div>
    <h2>{report.mode === 'demo' ? 'See how evidence changes the story.' : 'Your claim-by-claim report'}</h2>
    <p className="report-meta">Report ID: {report.id}</p><p className="notice">{report.note}</p>
    <p className="report-meta">{new Date(report.created_at).toLocaleString()} · {report.claims.length} claims · {report.usage.search_calls} searches · {report.usage.model_calls} model calls</p>
    {!report.claims.length && <p>No factual claims were researched. Classification: {report.intent}.</p>}
    {report.claims.map((claim, index) => <article className="claim-result" key={index}>
      <p className="eyebrow">CLAIM {index + 1} · {claim.status.toUpperCase()}</p>
      <h3>{claim.claim}</h3>
      <span className={`verdict verdict-${claim.verdict.toLowerCase().replaceAll(' ', '-')}`}>{report.mode === 'demo' ? 'EXAMPLE: ' : ''}{claim.verdict}</span>
      <p className="report-meta">{claim.sources_checked} pages retrieved · Confidence is not calibrated</p>
      <div className="evidence-grid">{claim.evidence.map((item, i) => <Evidence key={i} item={item} demo={report.mode === 'demo'} />)}</div>
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
            {item.url?.startsWith('https://') && <a href={item.url} target="_blank" rel="noopener noreferrer">Inspect source ↗</a>}
            {item.retrieved_at && <p className="verification">Retrieved: {new Date(item.retrieved_at).toLocaleString()}</p>}
          </details>
        </article>)}
      </details>}
      <details><summary>Research coverage & limitations</summary><p><strong>Supporting search:</strong> {claim.supporting_search}</p><p><strong>Contradicting search:</strong> {claim.contradicting_search}</p><ul>{claim.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
    </article>)}
    <aside className="report-limitations"><h3>Keep in mind</h3><ul>{report.limitations.map((text,i) => <li key={i}>{text}</li>)}</ul><p className="screen-only">Reports remain in this tab until you clear or reload it. Download JSON or choose Print / Save PDF to keep a copy.</p></aside>
  </section>;
}
