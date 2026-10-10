"""Agent response models. Their JSON schema is sent to the model as `response_format`.

Kept provider-friendly: every object is closed (additionalProperties: false), every field
is required (nullable where optional), and there are no dynamic keys or numeric/pattern
constraints in the schema. Range checks run in Pydantic validators instead, and the
orchestrator checks target legality and suspicion completeness.

Players are referred to by seat id ("p3") everywhere; prompts show "Ava (p3)".
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from societysim.roles import Role, SeerResult


def _probability(p: float) -> float:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must be between 0 and 1, got {p}")
    return p


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuspicionEntry(_Closed):
    player: str  # seat id
    p: Annotated[float, AfterValidator(_probability)]  # honest P(player is a wolf)


class PrivateReasoning(_Closed):
    thoughts: str
    suspicion: list[SuspicionEntry]  # every other living player, exactly once


class SeerReport(_Closed):
    target: str  # seat id of the checked player
    result: SeerResult
    night: int


class StatementAction(_Closed):
    text: str  # at most 80 words; longer text is truncated by the orchestrator
    accuse: list[str]  # seat ids
    claim_role: Role | None
    seer_report: SeerReport | None


class VoteAction(_Closed):
    vote: str  # seat id, or "abstain"


class WolfChatAction(_Closed):
    message: str
    proposed_target: str  # seat id of a living non-wolf


class TargetAction(_Closed):
    target: str  # seat id; used by the Seer and the Doctor


class StatementResponse(_Closed):
    private_reasoning: PrivateReasoning
    action: StatementAction


class VoteResponse(_Closed):
    private_reasoning: PrivateReasoning
    action: VoteAction


class WolfChatResponse(_Closed):
    private_reasoning: PrivateReasoning
    action: WolfChatAction


class TargetResponse(_Closed):
    private_reasoning: PrivateReasoning
    action: TargetAction


ABSTAIN: Literal["abstain"] = "abstain"

AgentResponse = StatementResponse | VoteResponse | WolfChatResponse | TargetResponse
