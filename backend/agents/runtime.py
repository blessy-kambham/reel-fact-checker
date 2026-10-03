"""The loop every agent runs.

An agent here is a model with a goal, a set of tools and a step budget. Each step the model is shown
the state of its work and chooses one tool. The application runs the tool, tells the agent what
happened, and the loop continues until a tool ends it or the steps run out.

The model only ever chooses; the application executes. That is what lets each agent act on its own
while the limits in its module (budgets, required checks, what it may read) stay enforced in code.

`AGENT_MODE` selects which agents work this way: `autonomous` (the default) for all of them, `fixed`
for none, or a comma-separated list of names from `AGENTS`. With an agent in fixed mode, or when its
planning call fails, the agent does its standard single pass instead.
"""
import asyncio
import json

from services.budget import BudgetExceeded
from services.providers import ProviderFailure

AGENTS = ('orchestrator', 'content_extractor', 'claim_extractor', 'research', 'analyst', 'citation_verifier', 'verdict')


def autonomous(provider, agent: str) -> bool:
    """Whether `agent` plans its own steps with this provider."""
    mode = str(getattr(provider, 'agent_mode', 'fixed')).strip().lower()
    if mode in ('autonomous', 'fixed'):
        return mode == 'autonomous'
    return agent in {name.strip() for name in mode.split(',')}


class PlanningUnavailable(Exception):
    """The model could not choose a step, so the agent falls back to its standard pass."""


class Done:
    """Returned by a tool to end the loop, carrying the agent's result."""
    def __init__(self, value=None):
        self.value = value


async def run_tools(provider, schema, instructions, state, tools, max_steps):
    """Let the model choose one tool per step.

    `schema` is the action the model returns; its `tool` field names the tool. `state(steps_left,
    last_step)` builds what the model sees. `tools[name](action)` runs the tool and returns a sentence
    describing what happened, which the agent sees on its next step, or `Done(value)` to end the loop.

    Returns the `Done` that ended the loop, or None when the steps ran out. A spending limit propagates;
    any other planning failure raises `PlanningUnavailable`.
    """
    last_step = ''
    for steps_left in range(max_steps, 0, -1):
        try:
            action = await provider.structured(schema, instructions, json.dumps(state(steps_left, last_step)))
        except BudgetExceeded:
            raise
        except (ProviderFailure, asyncio.TimeoutError) as exc:
            raise PlanningUnavailable() from exc
        outcome = await tools[action.tool](action)
        if isinstance(outcome, Done):
            return outcome
        last_step = outcome
    return None
