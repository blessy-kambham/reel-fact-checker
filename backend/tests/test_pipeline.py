import asyncio
import json
import socket
import pytest
from schemas import VerdictDecision, EvidenceRelation, ExtractionCoverage, AtomicClaim, Analysis, EvidenceDraft, EvidenceSelection, Source, CitationJudgment, Extraction
from services.pipeline import research_claim, run_pipeline, verify_citation
from services.providers import ProviderFailure
from services.fetcher import validate_url, PublicResolver

PAGE = 'The fictional sensor measured 12 units during the test. The sample was limited to one room.'
DRAFT = EvidenceSelection(source_id='S1', excerpt_id='S1:E1', statement='The sensor measured 12 units in the test.', stance='FOR')
CLAIM = AtomicClaim(text='The sensor measured 12 units.', context='Fictional test')

class FakeProvider:
    def __init__(self, draft=DRAFT, verdict='TRUE', approved=True, fail_search=False, claims=None):
        self.draft, self.verdict, self.approved, self.fail_search = draft, verdict, approved, fail_search
        self.usage, self.queries = {}, []
        self.claims = claims or [CLAIM]
        self.verifier_calls = 0
    async def search(self, query):
        self.queries.append(query)
        if self.fail_search:
            raise ProviderFailure('Search unavailable')
        return [{'url': 'https://example.org/report', 'title': 'Test report'}]
    async def structured(self, schema, instructions, data):
        if schema is ExtractionCoverage:
            return ExtractionCoverage(complete=True, issues=[])
        if schema is Extraction:
            return Extraction(intent='FACTUAL', claims=self.claims, omitted_claims=False, note='')
        if schema is Analysis:
            return Analysis(verdict=self.verdict, evidence=[self.draft], limitations=[])
        if schema is VerdictDecision:
            return VerdictDecision(verdict=self.verdict, evidence_ids=[e['id'] for e in json.loads(data)['verified_evidence']])
        if schema is EvidenceRelation:
            return EvidenceRelation(relation={'FOR':'SUPPORTS','AGAINST':'CONTRADICTS','CONTEXT':'BACKGROUND'}[self.draft.stance], reason='Scripted relation')
        self.verifier_calls += 1
        return CitationJudgment(supports_attribution=self.approved, stance_matches=True, reason='Test judgment')

async def fake_fetch(url):
    return url, PAGE

def test_both_directions_and_quote_check():
    provider = FakeProvider()
    result = asyncio.run(research_claim(CLAIM, provider, fake_fetch))
    assert result.verdict == 'TRUE' and result.evidence[0].verified
    assert len(provider.queries) == 2 and 'contradicting' in provider.queries[1]
    assert any('No verified contradicting' in s for s in result.limitations)

@pytest.mark.parametrize('draft', [DRAFT.model_copy(update={'source_id': 'invented'}), DRAFT.model_copy(update={'excerpt_id': 'S1:INVENTED'})])
def test_fabricated_citations_rejected(draft):
    provider = FakeProvider(draft=draft)
    result = asyncio.run(research_claim(CLAIM, provider, fake_fetch))
    assert result.verdict == 'UNVERIFIABLE' and result.status == 'incomplete'
    assert result.evidence == [] and provider.verifier_calls == 0

def test_semantic_rejection():
    result = asyncio.run(research_claim(CLAIM, FakeProvider(approved=False), fake_fetch))
    assert result.verdict == 'UNVERIFIABLE' and result.evidence == []

def test_search_failure():
    result = asyncio.run(research_claim(CLAIM, FakeProvider(fail_search=True), fake_fetch))
    assert result.verdict == 'UNVERIFIABLE'
    assert result.supporting_search == result.contradicting_search == 'Failed'

def test_false_requires_contradiction():
    assert asyncio.run(research_claim(CLAIM, FakeProvider(verdict='FALSE'), fake_fetch)).verdict == 'UNVERIFIABLE'

def test_no_snippet_fallback():
    async def unavailable(url):
        raise ValueError('blocked')
    result = asyncio.run(research_claim(CLAIM, FakeProvider(), unavailable))
    assert result.verdict == 'UNVERIFIABLE' and result.sources_checked == 0

def test_partial_failure():
    class Partial(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Analysis and json.loads(data)['claim']['text'] == 'Broken claim':
                raise ProviderFailure('Model unavailable')
            return await super().structured(schema, instructions, data)
    report = asyncio.run(run_pipeline('test', Partial(claims=[CLAIM, AtomicClaim(text='Broken claim', context='')]), fake_fetch))
    assert [c.verdict for c in report.claims] == ['TRUE', 'UNVERIFIABLE']

def test_parallelism_cap():
    async def scenario():
        active = peak = 0
        async def fetch(url):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return url, PAGE
        await run_pipeline('test', FakeProvider(claims=[CLAIM, CLAIM, CLAIM]), fetch)
        assert peak == 2
    asyncio.run(scenario())

def test_opinion_skips_research():
    class Opinion(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is ExtractionCoverage:
                return ExtractionCoverage(complete=True, issues=[])
            return Extraction(intent='OPINION', claims=[], omitted_claims=False, note='Opinion only')
    provider = Opinion()
    report = asyncio.run(run_pipeline('I like green.', provider, fake_fetch))
    assert not report.claims and not provider.queries

@pytest.mark.parametrize('url', ['http://example.com', 'https://127.0.0.1', 'https://[::1]', 'https://169.254.169.254', 'https://10.0.0.2', 'https://localhost', 'https://secret.local', 'https://user:password@example.com', 'file:///etc/passwd', 'https://example.com:8080'])
def test_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)

def test_public_url():
    validate_url('https://www.example.org/report')

def test_mixed_dns_blocked(monkeypatch):
    async def scenario():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        with pytest.raises(ValueError):
            await PublicResolver().resolve('example.org', 443)
    asyncio.run(scenario())

@pytest.mark.parametrize('source_id,quote,code', [
    ('invented', 'Known quote', 'unknown_source'),
    ('S1', 'Absent quote', 'quote_not_found'),
    ('S1', '  ', 'empty_quote'),
])
def test_rejected_citations_are_auditable(source_id, quote, code):
    draft = EvidenceDraft(source_id=source_id,quote=quote,statement='Test',stance='FOR')
    source = Source(id='S1',title='Fixture',url='https://example.org',text=PAGE,retrieved_at='2026-09-28')
    rejected = asyncio.run(verify_citation(draft, {'S1':source}, FakeProvider()))
    assert rejected.verification_code == code
    assert rejected.quote == draft.quote and not rejected.verified
    if code == 'unknown_source':
        assert rejected.url is None and rejected.source_text_sha256 is None
    else:
        assert rejected.retrieved_at
        assert len(rejected.source_text_sha256) == 64


def test_semantic_failure_is_distinct_from_quote_mismatch():
    result = asyncio.run(research_claim(CLAIM, FakeProvider(approved=False), fake_fetch))
    assert result.rejected_citations[0].verification_code == 'attribution_rejected'


def test_failed_verifier_preserves_previous_evidence():
    class FailingVerifier(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Analysis:
                return Analysis(verdict='TRUE', evidence=[DRAFT, DRAFT], limitations=[])
            if schema is CitationJudgment and self.verifier_calls:
                raise ProviderFailure('sensitive provider error must not be exposed')
            return await super().structured(schema, instructions, data)
    result = asyncio.run(research_claim(CLAIM, FailingVerifier(), fake_fetch))
    assert result.verdict == 'UNVERIFIABLE' and result.status == 'incomplete'
    assert len(result.evidence) == len(result.rejected_citations) == 1
    assert result.rejected_citations[0].verification_code == 'check_unavailable'
    assert 'sensitive provider error' not in result.model_dump_json()


def test_verified_citation_has_retrieval_fingerprint():
    import hashlib
    result = asyncio.run(research_claim(CLAIM, FakeProvider(), fake_fetch))
    citation = result.evidence[0]
    assert citation.verification_code == 'verified'
    assert citation.source_text_sha256 == hashlib.sha256(PAGE.encode()).hexdigest()
    assert result.rejected_citations == []


def test_omitted_false_assertion_withholds_partial_true_result():
    """Reproduce the live failure: only the true half survives extraction."""
    class IncompleteExtraction(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Extraction:
                return Extraction(intent='FACTUAL', claims=[CLAIM], omitted_claims=True,
                                  note='Second assertion was excluded as incorrect.')
            return await super().structured(schema, instructions, data)
    report = asyncio.run(run_pipeline('The sensor measured 12 units, so it works in every room.',
                                     IncompleteExtraction(), fake_fetch))
    assert report.omitted_claims
    assert report.claims[0].verdict == 'UNVERIFIABLE'
    assert report.claims[0].status == 'incomplete'
    assert report.claims[0].evidence == []
    assert report.coverage_status == 'incomplete'
    assert report.claims[0].claim == report.submitted_text
    assert json.loads(report.model_dump_json())['omitted_claims'] is True


def test_stance_mismatch_cannot_be_used_even_with_valid_attribution():
    class WrongStance(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is CitationJudgment:
                return CitationJudgment(supports_attribution=True, stance_matches=False,
                                        reason='Contradiction was incorrectly labeled FOR.')
            return await super().structured(schema, instructions, data)
    result = asyncio.run(research_claim(CLAIM, WrongStance(), fake_fetch))
    assert result.verdict == 'UNVERIFIABLE' and result.status == 'incomplete'
    assert not result.evidence
    assert result.rejected_citations[0].verification_code == 'attribution_rejected'


def test_complete_extraction_retains_verified_result():
    report = asyncio.run(run_pipeline(CLAIM.text, FakeProvider(), fake_fetch))
    assert not report.omitted_claims
    assert report.claims[0].verdict == 'TRUE'


@pytest.mark.parametrize('complete,issues', [(False, ['Missing false assertion']), (False, ['Changed date or negation']), (True, ['Missing assertion'])])
def test_unflagged_coverage_failure_stops_search(complete, issues):
    class Audit(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is ExtractionCoverage:
                assert json.loads(data)['submission'] == 'Original compound assertion'
                return ExtractionCoverage(complete=complete, issues=issues)
            return await super().structured(schema, instructions, data)
    provider = Audit()
    report = asyncio.run(run_pipeline('Original compound assertion', provider, fake_fetch))
    assert report.coverage_status == 'incomplete' and not report.omitted_claims
    assert report.claims[0].claim == report.submitted_text
    assert report.claims[0].verdict == 'UNVERIFIABLE'
    assert issues[0] in report.limitations
    assert not provider.queries


@pytest.mark.parametrize('error', [ProviderFailure('secret diagnostic'), asyncio.TimeoutError()])
def test_coverage_outage_stops_search(error):
    class Audit(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is ExtractionCoverage:
                raise error
            return await super().structured(schema, instructions, data)
    provider = Audit()
    report = asyncio.run(run_pipeline('A claim', provider, fake_fetch))
    assert report.coverage_status == 'unavailable'
    assert not provider.queries
    assert 'secret diagnostic' not in report.model_dump_json()


def test_false_nonfactual_classification_is_audited():
    class Audit(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Extraction:
                return Extraction(intent='OPINION', claims=[], omitted_claims=False, note='')
            if schema is ExtractionCoverage:
                return ExtractionCoverage(complete=False, issues=['Checkable assertion classified as opinion'])
            raise AssertionError('No research expected')
    provider = Audit()
    report = asyncio.run(run_pipeline('The Moon never rotates.', provider, fake_fetch))
    assert report.coverage_status == 'incomplete'
    assert report.claims[0].status == 'incomplete' and not provider.queries


@pytest.mark.parametrize('text', ['The Moon never rotates.', 'The Moon produces its own visible light like the Sun.', 'A is true, so B must be true.'])
def test_exact_copy_needs_no_semantic_audit(text):
    from services.pipeline import exact_submission_preserved
    extraction = Extraction(intent='FACTUAL', claims=[AtomicClaim(text=text, context=text)], omitted_claims=False, note='')
    assert exact_submission_preserved(text, extraction)


@pytest.mark.parametrize('claim,context', [('The Moon rotates.', ''), ('The Moon never rotates.', 'It is tidally locked.'), ('The Moon', '')])
def test_changed_text_or_invented_context_cannot_bypass_audit(claim, context):
    from services.pipeline import exact_submission_preserved
    extraction = Extraction(intent='FACTUAL', claims=[AtomicClaim(text=claim, context=context)], omitted_claims=False, note='')
    assert not exact_submission_preserved('The Moon never rotates.', extraction)
