"""A fictional, fixed walkthrough. Never applied to user-submitted claims."""
from schemas import Report, ClaimResult, Citation
from services.pipeline import now
from uuid import uuid4

DEMO_TEXT = 'The fictional town of Exampleville cut all traffic by 40% after its bus pilot.'


def demo_report() -> Report:
    return Report(id=str(uuid4()), mode='demo', submitted_text=DEMO_TEXT, created_at=now(), intent='FICTIONAL WALKTHROUGH',
        note='All names, figures, and source excerpts below are invented fixtures. No web search or AI calls were made.',
        claims=[ClaimResult(claim=DEMO_TEXT, verdict='MISLEADING', status='complete', sources_checked=0,
            supporting_search='Simulated using a local fixture; no search was performed.',
            contradicting_search='Simulated using a local fixture; no search was performed.',
            limitations=['Illustrates how a statistic can lose its original scope. This is not a real-world verdict.',
                         'No calibrated confidence score is shown.'],
            evidence=[Citation(source_id='FIXTURE-1', title='Fictional pilot summary — local fixture', url=None,
                quote='Weekday car trips on Pilot Street fell 40% during the two-week trial.',
                statement='The fictional 40% figure covers one street and a two-week trial.', stance='FOR',
                verified=False, verification='Illustrative fixture only; not independently verified.'),
                Citation(source_id='FIXTURE-2', title='Fictional townwide count — local fixture', url=None,
                quote='Townwide traffic fell 4% during the trial. The study did not isolate the effect of the bus pilot.',
                statement='The fictional townwide figure is smaller, and the pilot’s causal effect was not established.', stance='AGAINST',
                verified=False, verification='Illustrative fixture only; not independently verified.')])],
        limitations=['Demo only. Submit your own text in live mode after API configuration.'],
        usage={'model_calls': 0, 'search_calls': 0, 'input_tokens': 0, 'output_tokens': 0})
