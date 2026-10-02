import React, { useState } from 'react';

// Saved live reports, stored only in the backend's local SQLite file.
export default function History({ apiBase, onOpen, disabled }) {
  const [items, setItems] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function request(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, { signal: AbortSignal.timeout(10000), ...options });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'History request failed.');
    return data;
  }

  async function load() {
    setBusy(true); setMessage('');
    try { setItems((await request('/history')).reports); }
    catch (err) { setMessage(err instanceof TypeError ? 'Could not reach the server.' : err.message); }
    finally { setBusy(false); }
  }

  async function open(id) {
    setBusy(true); setMessage('');
    try { onOpen(await request(`/history/${id}`)); }
    catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }

  async function remove(id) {
    if (!window.confirm('Delete this saved report? This cannot be undone.')) return;
    setBusy(true); setMessage('');
    try { await request(`/history/${id}`, { method: 'DELETE' }); setItems(items.filter(item => item.id !== id)); }
    catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }

  return <details className="history" onToggle={event => { if (event.currentTarget.open && items === null) load(); }}>
    <summary>Saved reports</summary>
    <p className="report-meta">Your past reports. Delete any you no longer need.</p>
    {message && <p className="error" role="alert">{message}</p>}
    {items?.length === 0 && <p className="report-meta">No saved reports yet.</p>}
    <ul>{items?.map(item => <li key={item.id} className="history-item">
      <p className="report-meta">{new Date(item.created_at).toLocaleString()} · {item.claims.map(c => c.verdict).join(', ') || item.intent}</p>
      <p>{item.preview}</p>
      <div className="report-actions">
        <button type="button" className="small-button" disabled={busy || disabled} onClick={() => open(item.id)}>Open</button>
        <button type="button" className="small-button" disabled={busy || disabled} onClick={() => remove(item.id)}>Delete</button>
      </div>
    </li>)}</ul>
    {items !== null && <button type="button" className="small-button" disabled={busy} onClick={load}>{busy ? 'Loading…' : 'Refresh'}</button>}
  </details>;
}
