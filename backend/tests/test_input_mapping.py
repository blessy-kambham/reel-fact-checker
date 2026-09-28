import pytest
from schemas import AtomicClaim, Extraction
from services.input_mapping import map_input


def extraction(text, parts):
    return Extraction(intent='FACTUAL', claims=[AtomicClaim(text=p, context=text) for p in parts], omitted_claims=False, note='')


def test_compound_false_assertion_maps_without_truth_judgment():
    text = 'We see the same side of the Moon, so the other side never receives sunlight.'
    parts = ['We see the same side of the Moon', 'the other side never receives sunlight.']
    spans = map_input(text, extraction(text, parts))
    assert [text[s['start']:s['end']] for s in spans] == parts


@pytest.mark.parametrize('text,parts', [
    ('A is true, so B is false.', ['A is true']),
    ('A is not true.', ['A is true.']),
    ('A is true unless B is false.', ['A is true', 'B is false.']),
    ('A is true or B is false.', ['A is true', 'B is false.']),
    ('A is true, so', ['A is true']),
    ('A is true. B is false.', ['B is false.', 'A is true.']),
])
def test_mapping_rejects_lost_or_rewritten_meaning(text, parts):
    assert map_input(text, extraction(text, parts)) is None


def test_mapping_requires_original_context_for_split_claims():
    text = 'A is true, so B is false.'
    result = extraction(text, ['A is true', 'B is false.'])
    result.claims[1].context = ''
    assert map_input(text, result) is None


def test_pipeline_exports_mapping_without_model_coverage_judgment():
    import asyncio
    from services.pipeline import run_pipeline
    from schemas import ExtractionCoverage
    from tests.test_pipeline import FakeProvider, fake_fetch
    text = 'We see the same side of the Moon, so the other side never receives sunlight.'
    extracted = extraction(text, ['We see the same side of the Moon', 'the other side never receives sunlight.'])
    class Provider(FakeProvider):
        async def structured(self, schema, instructions, data):
            if schema is Extraction:
                return extracted
            if schema is ExtractionCoverage:
                raise AssertionError('Verbatim mapped input must not require a truth-prone coverage judgment')
            return await super().structured(schema, instructions, data)
    report = asyncio.run(run_pipeline(text, Provider(), fake_fetch))
    assert report.coverage_status == 'passed'
    assert len(report.input_spans) == len(report.claims) == 2
    assert [span.text for span in report.input_spans] == [claim.text for claim in extracted.claims]
