"""The research agent's planning loop: it chooses its own tools, inside limits the application enforces. Offline."""
import asyncio
import json
import pytest
from agents import research_agent
from agents.orchestrator import research_all, research_claim
from agents.research_agent import MAX_SEARCHES, MAX_STEPS
from schemas import ResearchAction
from services.budget import BudgetExceeded
from tools.providers import ProviderFailure, Providers
from tests.test_pipeline import CLAIM, PAGE, FakeProvider


def search(query, looking_for='supporting', reason='Find evidence.'):
    return ResearchAction(tool='search_web', query=query, looking_for=looking_for, result_ids=[], reason=reason)


def read(*ids):
    return ResearchAction(tool='read_pages', query='', looking_for='supporting', result_ids=list(ids), reason='Read them.')


def finish(reason='Both sides are covered.'):
    return ResearchAction(tool='finish', query='', looking_for='supporting', result_ids=[], reason=reason)


class Planner(FakeProvider):
    """A provider whose model plans research with a scripted sequence of tool choices."""
    agent_mode = 'research'

    def __init__(self, actions, results_per_search=1, **kwargs):
        super().__init__(**kwargs)
        self.actions, self.states, self.results_per_search = list(actions), [], results_per_search

    async def search(self, query):
        self.queries.append(query)
        number = len(self.queries)
        return [{'url': f'https://example.org/page-{number}-{i}', 'title': f'Result {number}.{i}',
                 'content': 'A short snippet.', 'raw_content': 'RAW PAGE BODY ' * 40}
                for i in range(self.results_per_search)]

    async def structured(self, schema, instructions, data):
        if schema is ResearchAction:
            self.states.append(json.loads(data))
            return self.actions.pop(0) if self.actions else finish('Nothing more to do.')
        return await super().structured(schema, instructions, data)


def fetcher():
    fetched = []
    async def fetch(url):
        fetched.append(url)
        return url, PAGE
    fetch.fetched = fetched
    return fetch


def run(provider, fetch=None):
    return asyncio.run(research_claim(CLAIM, provider, fetch or fetcher()))


def steps(result):
    """The research agent's lines from the claim's step list."""
    return [line.removeprefix('Research Agent: ') for line in result.agent_steps if line.startswith('Research Agent: ')]


def test_the_agent_writes_its_own_queries_and_chooses_what_to_read():
    provider = Planner([search('sensor reading 12 units test report'), read('R1'),
                        search('sensor reading disputed', 'contradicting'), read('R2'), finish()])
    fetch = fetcher()
    result = run(provider, fetch)
    assert provider.queries == ['sensor reading 12 units test report', 'sensor reading disputed']
    assert fetch.fetched == ['https://example.org/page-1-0', 'https://example.org/page-2-0']
    assert result.verdict == 'TRUE' and result.sources_checked == 2
    assert result.supporting_search == result.contradicting_search == research_agent.RETRIEVED
    assert steps(result) == [
        'Searched for supporting evidence: "sensor reading 12 units test report" (1 new result(s)).',
        'Read 1 result(s) (R1); 1 kept as evidence.',
        'Searched for contradicting evidence: "sensor reading disputed" (1 new result(s)).',
        'Read 1 result(s) (R2); 1 kept as evidence.',
        'Finished: Both sides are covered.']


def test_each_step_shows_the_agent_the_result_of_the_last_one():
    provider = Planner([search('first query'), read('R1'), search('second query', 'contradicting'), read('R2'), finish()])
    run(provider)
    first, second, third = provider.states[0], provider.states[1], provider.states[2]
    assert first['searches'] == [] and set(first['tools']) == {'search_web', 'read_pages', 'finish'}
    assert second['searches'][0]['results'][0] == {'id': 'R1', 'title': 'Result 1.0', 'url': 'https://example.org/page-1-0',
                                                   'source_type': 'Unrated website', 'snippet': 'A short snippet.', 'read': False}
    assert 'returned 1 new result' in second['last_step']
    assert third['pages_read'][0]['source_id'] == 'S1' and third['searches'][0]['results'][0]['read'] is True
    assert third['remaining'] == {'searches': MAX_SEARCHES - 1, 'pages': research_agent.MAX_PAGES - 1, 'steps': MAX_STEPS - 2}
    # Page bodies never go into planning: only titles, snippets and a short opening.
    assert all('RAW PAGE BODY' not in json.dumps(state) for state in provider.states)


def test_finishing_is_refused_until_contradicting_evidence_has_been_searched_for():
    provider = Planner([search('supporting query'), read('R1'), finish('Enough.'),
                        search('contradicting query', 'contradicting'), read('R2'), finish('Now both.')])
    result = run(provider)
    assert 'finish refused: search for contradicting evidence first.' in [s['last_step'] for s in provider.states]
    assert provider.queries == ['supporting query', 'contradicting query']
    assert steps(result)[-1] == 'Finished: Now both.'


def test_a_direction_the_agent_never_searches_is_searched_for_it():
    provider = Planner([search('only supporting'), read('R1')] + [finish('Done.')] * MAX_STEPS)
    result = run(provider)
    assert provider.queries[0] == 'only supporting'
    assert len(provider.queries) == 2 and provider.queries[1].endswith('contradicting evidence limitations fact check')
    assert result.contradicting_search == research_agent.RETRIEVED and result.sources_checked == 2
    assert 'Stopped: the step budget for this claim was spent.' in steps(result)
    assert any('standard search for contradicting evidence' in step for step in steps(result))


def test_unread_results_are_read_when_the_agent_reads_nothing():
    provider = Planner([search('a'), search('b', 'contradicting'), finish()])
    result = run(provider)
    assert result.sources_checked == 2 and result.verdict == 'TRUE'


def test_search_budget_is_a_hard_cap_and_keeps_room_for_the_other_direction():
    provider = Planner([search(f'supporting query {i}') for i in range(MAX_STEPS)])
    run(provider)
    supporting = [q for q in provider.queries if q.startswith('supporting query')]
    assert len(provider.queries) == MAX_SEARCHES and len(supporting) == MAX_SEARCHES - 1
    assert provider.queries[-1].endswith('contradicting evidence limitations fact check')
    assert any('use the remaining search for contradicting evidence' in s['last_step'] for s in provider.states)


def test_repeated_and_empty_queries_are_refused():
    provider = Planner([search('same'), search('SAME'), search('   '), search('other', 'contradicting'), finish()])
    run(provider)
    assert provider.queries == ['same', 'other']
    refusals = [s['last_step'] for s in provider.states if 'empty or already used' in s['last_step']]
    assert len(refusals) == 2


def test_the_agent_can_only_read_pages_a_search_returned():
    provider = Planner([search('a'), read('https://internal.example/secret', 'R99'), read('r1'),
                        search('b', 'contradicting'), finish()])
    fetch = fetcher()
    run(provider, fetch)
    assert 'https://internal.example/secret' not in fetch.fetched
    assert fetch.fetched[0] == 'https://example.org/page-1-0'  # 'r1' is accepted case-insensitively.
    assert any('none of those IDs is an unread search result' in s['last_step'] for s in provider.states)


def test_page_budget_is_enforced():
    provider = Planner([search(f'q{i}', 'supporting' if i % 2 else 'contradicting') for i in range(MAX_SEARCHES)]
                       + [read('R1', 'R2', 'R3'), read('R4', 'R5', 'R6'), read('R7', 'R8', 'R9'), read('R10'), finish()],
                       results_per_search=3)
    result = run(provider)
    assert result.sources_checked == research_agent.MAX_PAGES


def test_planning_failure_falls_back_to_the_fixed_plan():
    class Broken(Planner):
        async def structured(self, schema, instructions, data):
            if schema is ResearchAction:
                raise ProviderFailure('Model unavailable')
            return await super().structured(schema, instructions, data)
    provider = Broken([])
    result = run(provider)
    assert len(provider.queries) == 2 and 'primary sources' in provider.queries[0] and 'contradicting' in provider.queries[1]
    assert result.verdict == 'TRUE' and steps(result) == []
    assert any('could not plan a step' in text for text in result.limitations)


def test_a_failed_search_withholds_the_verdict_as_before():
    class Flaky(Planner):
        async def search(self, query):
            if 'contradict' in query:
                self.queries.append(query)
                raise ProviderFailure('Search request failed; this direction was not fully researched.')
            return await super().search(query)
    result = run(Flaky([search('supporting'), read('R1'), search('contradicting angle', 'contradicting'), finish()]))
    assert result.withheld_reason == 'search_failed' and result.contradicting_search == 'Failed'
    assert 'Search for contradicting evidence failed: "contradicting angle".' in steps(result)


def test_spending_limit_during_planning_stops_the_claim():
    class Capped(Planner):
        async def structured(self, schema, instructions, data):
            if schema is ResearchAction:
                raise BudgetExceeded("Today's spending limit has been reached.")
            return await super().structured(schema, instructions, data)
    [result] = asyncio.run(research_all([CLAIM], Capped([]), fetcher()))
    assert result.withheld_reason == 'spending_limit' and result.verdict == 'UNVERIFIABLE'


def test_providers_without_planning_use_the_fixed_plan():
    provider = FakeProvider()
    result = run(provider)
    assert len(provider.queries) == 2 and steps(result) == []


def test_fixed_mode_can_be_selected_by_setting(monkeypatch):
    from agents.runtime import AGENTS, autonomous
    class Configured:
        agent_mode = Providers.agent_mode
    monkeypatch.delenv('AGENT_MODE', raising=False)
    assert all(autonomous(Configured(), agent) for agent in AGENTS)
    monkeypatch.setenv('AGENT_MODE', 'fixed')
    assert not any(autonomous(Configured(), agent) for agent in AGENTS)
    monkeypatch.setenv('AGENT_MODE', 'research, verdict')
    assert [agent for agent in AGENTS if autonomous(Configured(), agent)] == ['research', 'verdict']


def test_planning_calls_are_budgeted_and_traced_by_the_validation_runner(monkeypatch, tmp_path):
    from evaluation.live import AuditProvider, Budget
    planner = Planner([search('own query'), read('R1'), search('other side', 'contradicting'), read('R2'), finish()])
    async def structured(provider, schema, instructions, data):
        provider.usage['model_calls'] += 1
        return await planner.structured(schema, instructions, data)
    async def web_search(provider, query):
        return await planner.search(query)
    monkeypatch.setattr(Providers, 'structured', structured)
    monkeypatch.setattr(Providers, 'search', web_search)
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    monkeypatch.setenv('AGENT_MODE', 'research')
    trace = {'model': [], 'searches': []}
    result = asyncio.run(research_claim(CLAIM, AuditProvider(Budget(tmp_path / 'ledger.json', search_limit=4), trace), fetcher()))
    stages = [record['stage'] for record in trace['model']]
    assert stages[:5] == ['ResearchAction'] * 5 and stages[5] == 'Analysis'
    assert [record['query'] for record in trace['searches']] == ['own query', 'other side']
    assert trace['model'][0]['output']['tool'] == 'search_web' and result.verdict_state == 'issued'


@pytest.mark.parametrize('tool', sorted(research_agent.TOOLS))
def test_every_tool_is_described_to_the_agent(tool):
    assert tool in ResearchAction.model_fields['tool'].annotation.__args__
