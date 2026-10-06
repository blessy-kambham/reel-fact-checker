"""Local report history in SQLite (standard library only).

Each live report is stored whole as JSON, the source of truth for re-display, plus
queryable rows for claims and citations. Nothing is sent anywhere; the file stays on this machine.

The claim rows also let a repeated claim be recognised: `find_claim` returns the result an
identical claim received in an earlier report, so it can be shown again without new research.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from schemas import ClaimResult, Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    submitted_text TEXT NOT NULL,
    intent TEXT NOT NULL,
    coverage_status TEXT NOT NULL,
    model_calls INTEGER NOT NULL DEFAULT 0,
    search_calls INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    claim TEXT NOT NULL,
    verdict TEXT NOT NULL,
    verdict_state TEXT NOT NULL,
    withheld_reason TEXT,
    status TEXT NOT NULL,
    claim_key TEXT,
    PRIMARY KEY (report_id, position)
);
CREATE TABLE IF NOT EXISTS citations (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    claim_position INTEGER NOT NULL,
    evidence_id TEXT,
    accepted INTEGER NOT NULL,
    used_for_verdict INTEGER NOT NULL,
    stance TEXT NOT NULL,
    url TEXT,
    title TEXT NOT NULL,
    retrieved_at TEXT,
    source_text_sha256 TEXT,
    verification_code TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_created ON reports(created_at DESC);
"""
PREVIEW_CHARS = 200


class History:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(SCHEMA)
        # Histories created before repeated claims were recognised have no key column yet.
        if 'claim_key' not in {row[1] for row in db.execute('PRAGMA table_info(claims)')}:
            try:
                db.execute('ALTER TABLE claims ADD COLUMN claim_key TEXT')
            except sqlite3.OperationalError:
                pass  # another request opening the same history added it first
        db.execute('CREATE INDEX IF NOT EXISTS claims_key ON claims(claim_key)')
        return db

    def save(self, report: Report) -> None:
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
                report.id, report.created_at, report.mode, report.submitted_text, report.intent,
                report.coverage_status, report.usage.get('model_calls', 0), report.usage.get('search_calls', 0),
                report.model_dump_json()))
            db.execute('DELETE FROM claims WHERE report_id = ?', (report.id,))
            db.execute('DELETE FROM citations WHERE report_id = ?', (report.id,))
            for position, claim in enumerate(report.claims):
                # A result shown again is not stored under its key, so reuse always goes back to the original check
                # and cannot outlive it.
                db.execute('INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
                    report.id, position, claim.claim, claim.verdict, claim.verdict_state, claim.withheld_reason, claim.status,
                    None if claim.reused_from else claim.claim_key))
                used = set(claim.verdict_evidence_ids) if claim.verdict_state == 'issued' else set()
                for accepted, citations in ((1, claim.evidence), (0, claim.rejected_citations)):
                    for c in citations:
                        db.execute('INSERT INTO citations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (
                            report.id, position, c.evidence_id, accepted, int(bool(accepted and c.evidence_id in used)),
                            c.stance, c.url, c.title, c.retrieved_at, c.source_text_sha256, c.verification_code))

    def recent(self, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute('SELECT id, created_at, submitted_text, intent, coverage_status FROM reports '
                              'ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
            items = []
            for report_id, created_at, text, intent, coverage in rows:
                claims = db.execute('SELECT claim, verdict, verdict_state FROM claims WHERE report_id = ? ORDER BY position',
                                    (report_id,)).fetchall()
                items.append({'id': report_id, 'created_at': created_at, 'intent': intent, 'coverage_status': coverage,
                              'preview': text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1] + '…',
                              'claims': [{'claim': c, 'verdict': v, 'verdict_state': s} for c, v, s in claims]})
            return items

    def get(self, report_id: str) -> Report | None:
        with closing(self._connect()) as db:
            row = db.execute('SELECT report_json FROM reports WHERE id = ?', (report_id,)).fetchone()
        return Report.model_validate(json.loads(row[0])) if row else None

    def find_claim(self, key: str, since: str):
        """The newest result an identical claim received in a report created at or after `since` (an ISO
        timestamp), as (result, report ID, report time), or None. Only a real verdict issued on complete
        research is returned: a withheld verdict, or an answer of UNVERIFIABLE, is worth researching again."""
        with closing(self._connect()) as db:
            row = db.execute(
                'SELECT r.report_json, c.position, r.id, r.created_at FROM claims c JOIN reports r ON r.id = c.report_id '
                "WHERE c.claim_key = ? AND c.verdict_state = 'issued' AND c.verdict != 'UNVERIFIABLE' "
                "AND c.status = 'complete' AND r.mode = 'live' "
                'AND r.created_at >= ? ORDER BY r.created_at DESC LIMIT 1', (key, since)).fetchone()
        if row is None:
            return None
        claims = json.loads(row[0]).get('claims', [])
        return (ClaimResult.model_validate(claims[row[1]]), row[2], row[3]) if row[1] < len(claims) else None

    def delete(self, report_id: str) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute('DELETE FROM reports WHERE id = ?', (report_id,)).rowcount > 0
