"""Offline checks for live-validation preparation. No test here makes a network or paid call."""
import asyncio
import json
import subprocess
from decimal import Decimal
import pytest
from evaluation import live, snapshot
from evaluation.live import AuditProvider, Budget, open_allowance
from evaluation.summarize import summarize
from services.pipeline import research_claim
from services.budget import BudgetExceeded
from services.providers import Providers, ProviderFailure
from tests.test_pipeline import CLAIM, FakeProvider, fake_fetch

SECRET = 'sk-test-must-never-be-copied'


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    for path, text in {
        'backend/main.py': 'tracked = True\n',
        'backend/tests/test_new.py': 'untracked = True\n',
        'backend/.env': f'OPENAI_API_KEY={SECRET}\n',
        'backend/.env.local': f'OPENAI_API_KEY={SECRET}\n',
        'backend/.env.example': 'OPENAI_API_KEY=\n',
        'backend/evaluation/results/live-1.json': '{}',
        'frontend/src/main.jsx': 'export {}\n',
        'frontend/node_modules/pkg/index.js': 'ignored\n',
        'docs/notes.md': 'not code\n',
        '.gitignore': '.env\n.env.*\n!.env.example\nnode_modules/\n/backend/evaluation/results/\n',
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    git = lambda *args: subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    git('init', '-q')
    git('add', 'backend/main.py', 'frontend/src/main.jsx', '.gitignore')
    git('-c', 'user.name=t', '-c', 'user.email=t@example.org', 'commit', '-qm', 'init')
    return root


def test_snapshot_includes_untracked_source_and_excludes_secrets(repo):
    files = snapshot.source_files(repo)
    assert 'backend/tests/test_new.py' in files and 'backend/main.py' in files
    assert 'backend/.env.example' in files and 'frontend/src/main.jsx' in files
    assert not any(name.endswith(('.env', '.env.local')) for name in files)
    assert not any('results' in name or 'node_modules' in name or name.startswith('docs/') for name in files)


def test_snapshot_force_excludes_secrets_even_if_tracked(repo):
    subprocess.run(['git', '-C', str(repo), 'add', '-f', 'backend/.env'], check=True)
    assert 'backend/.env' not in snapshot.source_files(repo)


def test_capture_copies_exact_code_without_secrets_or_locks(repo, tmp_path):
    info = snapshot.capture(tmp_path / 'results', repo)
    target = tmp_path / 'results' / 'snapshots' / info['fingerprint'][:16]
    copied = {p.relative_to(target / 'files').as_posix() for p in (target / 'files').rglob('*') if p.is_file()}
    assert copied == set(info['files'])
    assert all(SECRET not in p.read_text() for p in target.rglob('*') if p.is_file())
    assert 'backend/tests/test_new.py' in info['untracked_files']
    assert info['git_head'] and not (repo / '.git' / 'index.lock').exists()


def test_fingerprint_changes_only_when_code_changes(repo):
    first = snapshot.manifest(repo)['fingerprint']
    (repo / 'docs/notes.md').write_text('edited docs do not change behaviour\n')
    (repo / 'backend/.env').write_text('OPENAI_API_KEY=rotated\n')
    assert snapshot.manifest(repo)['fingerprint'] == first
    (repo / 'backend/tests/test_new.py').write_text('untracked = False\n')
    assert snapshot.manifest(repo)['fingerprint'] != first


def test_allowance_is_tied_to_code_and_limits_cannot_be_raised(tmp_path):
    budget = open_allowance('retest-1', 'a' * 64, '0.05', 4, tmp_path)
    budget.reserve_search()
    resumed = open_allowance('retest-1', 'a' * 64, '0.50', 20, tmp_path)
    assert resumed.searches == 1 and resumed.search_limit == 4 and resumed.max_usd == Decimal('0.05')
    with pytest.raises(RuntimeError, match='different code'):
        open_allowance('retest-1', 'b' * 64, '0.05', 4, tmp_path)
    fresh = open_allowance('retest-2', 'b' * 64, '0.05', 4, tmp_path)
    assert fresh.searches == 0 and fresh.metadata['code_fingerprint'] == 'b' * 64


@pytest.mark.parametrize('name, usd, searches', [
    ('../escape', '0.05', 4), ('Upper', '0.05', 4), ('', '0.05', 4),
    ('ok', '0', 4), ('ok', '5', 4), ('ok', '0.05', -1), ('ok', '0.05', 500)])
def test_allowance_rejects_unsafe_names_and_limits(tmp_path, name, usd, searches):
    with pytest.raises(ValueError):
        open_allowance(name, 'a' * 64, usd, searches, tmp_path)
    assert not (tmp_path / 'allowances').exists()


def test_legacy_ledger_is_never_reset_or_reused(tmp_path):
    legacy = tmp_path / 'budget.json'
    legacy.write_text(json.dumps({'reserved': '0.0999', 'searches': 12, 'search_limit': 12, 'stopped': True}))
    before = legacy.read_text()
    open_allowance('new-approval', 'a' * 64, '0.05', 4, tmp_path)
    assert legacy.read_text() == before
    rows = live.allowance_status(tmp_path)
    assert {r['name'] for r in rows} == {'new-approval', '(legacy budget.json, not used)'}


class Offline:
    """Stands in for the paid provider methods underneath AuditProvider."""
    def __init__(self, monkeypatch):
        self.fake, self.calls = FakeProvider(), []
        offline = self
        # These tests pin the call sequence of the fixed research plan; the planning loop has its own tests.
        monkeypatch.setenv('RESEARCH_AGENT_MODE', 'fixed')
        async def structured(provider, schema, instructions, data):
            offline.calls.append(schema.__name__)
            provider.usage['model_calls'] += 1
            return await offline.fake.structured(schema, instructions, data)
        async def search(provider, query):
            offline.calls.append('search')
            return await offline.fake.search(query)
        monkeypatch.setattr(Providers, 'structured', structured)
        monkeypatch.setattr(Providers, 'search', search)


def audit_run(monkeypatch, budget):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    trace = {'model': [], 'searches': []}
    result = asyncio.run(research_claim(CLAIM, AuditProvider(budget, trace), fake_fetch))
    return result, trace


def test_relation_and_verdict_calls_are_reserved_and_traced(monkeypatch, tmp_path):
    offline = Offline(monkeypatch)
    budget = Budget(tmp_path / 'ledger.json', search_limit=4)
    result, trace = audit_run(monkeypatch, budget)
    stages = [record['stage'] for record in trace['model']]
    assert stages == ['Analysis', 'EvidenceRelation', 'CitationJudgment', 'VerdictDecision']
    assert offline.calls == ['search', 'search'] + stages
    assert result.verdict_state == 'issued' and result.verdict_evidence_ids == ['E1']
    # The trace shows which verified evidence the verdict relied on.
    verdict_record = trace['model'][-1]
    assert [e['id'] for e in verdict_record['input']['verified_evidence']] == ['E1']
    assert verdict_record['output']['evidence_ids'] == ['E1']
    persisted = json.loads((tmp_path / 'ledger.json').read_text())
    assert Decimal(persisted['reserved']) == budget.reserved > 0 and persisted['searches'] == 2


def audit_report(monkeypatch, budget):
    from services.pipeline import run_pipeline
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    trace = {'model': [], 'searches': []}
    report = asyncio.run(run_pipeline(CLAIM.text, AuditProvider(budget, trace), fake_fetch))
    return report.claims[0], trace


def test_budget_stop_before_verdict_is_a_named_withheld_verdict_and_persists(monkeypatch, tmp_path):
    Offline(monkeypatch)
    # Reservations are deterministic offline; allow everything except the final verdict call.
    costs = []
    original = Budget.reserve
    def recording(self, instructions, data, schema):
        cost = original(self, instructions, data, schema)
        costs.append((schema.__name__, cost))
        return cost
    monkeypatch.setattr(Budget, 'reserve', recording)
    audit_report(monkeypatch, Budget())
    monkeypatch.setattr(Budget, 'reserve', original)
    assert [stage for stage, _ in costs][-1] == 'VerdictDecision'
    limit = sum(cost for stage, cost in costs if stage != 'VerdictDecision') + Decimal('0.0000001')
    ledger = tmp_path / 'ledger.json'
    offline = Offline(monkeypatch)
    claim, trace = audit_report(monkeypatch, Budget(ledger, search_limit=4, max_usd=limit))
    assert claim.withheld_reason == 'spending_limit' and claim.verdict == 'UNVERIFIABLE'
    assert trace['blocked'] == [{'stage': 'VerdictDecision', 'reason': 'budget_stopped'}]
    assert 'VerdictDecision' not in offline.calls
    assert Budget(ledger).stopped

    # Restart: the persisted stop refuses the first call of a new report, and the limit never rises.
    offline = Offline(monkeypatch)
    with pytest.raises(BudgetExceeded):
        audit_report(monkeypatch, Budget(ledger, search_limit=4, max_usd='0.10'))
    assert offline.calls == []
    assert Budget(ledger).max_usd == limit


def test_search_cap_stops_the_claim_before_any_model_call(monkeypatch):
    offline = Offline(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    trace = {'model': [], 'searches': []}
    budget = Budget(search_limit=1)
    with pytest.raises(BudgetExceeded, match='Search limit'):
        asyncio.run(research_claim(CLAIM, AuditProvider(budget, trace), fake_fetch))
    assert offline.calls == ['search'] and budget.stopped
    assert trace['blocked'] == [{'stage': 'search', 'reason': 'budget_stopped'}]


def test_budget_stop_inside_a_report_is_named(monkeypatch):
    Offline(monkeypatch)
    claim, _ = audit_report(monkeypatch, Budget(search_limit=1))
    assert claim.verdict == 'UNVERIFIABLE' and claim.withheld_reason == 'spending_limit'


def test_validation_client_never_retries(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    assert AuditProvider(Budget(), {'model': [], 'searches': []}).client.max_retries == 0


def test_prepare_is_offline_and_never_reports_secret_values(monkeypatch, repo, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', SECRET)
    monkeypatch.setenv('TAVILY_API_KEY', SECRET)
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    def no_provider(*args, **kwargs):
        raise AssertionError('prepare must not construct a paid provider')
    monkeypatch.setattr(live, 'AuditProvider', no_provider)
    monkeypatch.setattr(Providers, '__init__', no_provider)
    report = live.prepare(tmp_path / 'results', repo)
    text = json.dumps(report)
    assert SECRET not in text and report['missing_settings'] == [] and report['model_supported']
    assert report['code_fingerprint'] == snapshot.manifest(repo)['fingerprint']


def test_cli_defaults_to_offline_prepare_and_requires_explicit_limits(monkeypatch, capsys):
    monkeypatch.setattr(live, 'prepare', lambda: {'code_fingerprint': 'f'})
    live.main([])
    assert 'Offline preparation only' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        live.main(['--allow-paid'])
    with pytest.raises(SystemExit):
        live.main(['--allow-paid', '--allowance', 'x', '--max-usd', '0.05'])


def test_summary_counts_issued_and_withheld_verdicts():
    claims = [{'claim': 'A', 'verdict': 'TRUE', 'status': 'complete', 'evidence': [], 'verdict_state': 'issued',
               'withheld_reason': None, 'verdict_evidence_ids': ['E1']},
              {'claim': 'B', 'verdict': 'UNVERIFIABLE', 'status': 'incomplete', 'evidence': [], 'verdict_state': 'withheld',
               'withheld_reason': 'unknown_evidence_ids', 'decision_verdict': 'TRUE', 'verdict_evidence_ids': []}]
    result = summarize([{'response': {'claims': claims}, 'code_fingerprint': 'abc', 'allowance': 'retest-1'}], [])
    assert result['issued_verdicts'] == 1
    assert result['single_source_verdicts'] == 0
    assert result['withheld_reasons'] == {'unknown_evidence_ids': 1}
    assert result['code_fingerprints'] == ['abc']
    assert result['results'][0]['claims'][1]['decision_verdict'] == 'TRUE'


def test_every_paid_research_route_is_traced():
    import main
    research_routes = {route.path for route in main.app.routes
                       if 'POST' in getattr(route, 'methods', set()) and route.path.startswith('/fact-check')}
    assert research_routes == {'/fact-check', '/fact-check-article', '/fact-check-video'}
    assert research_routes <= live.LIVE_PATHS
