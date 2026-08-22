"""Question tool — ask the user questions mid-run (human intervention).

Port of opencode's question tool (packages/opencode/src/tool/question.ts +
packages/schema/src/v1/question.ts). The model emits one or more questions,
each with a header, options, and optional multi-select/custom-answer; the
tool blocks on the user's reply (here via the ToolContext.ask_question
callback) and feeds the answers back as the tool result so the model can
continue with the user's choices in mind.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult


class QuestionOption(BaseModel):
    label: str = Field(description="Short label for this option.")
    description: str = Field(default="", description="Longer explanation of the option.")


class QuestionPrompt(BaseModel):
    question: str = Field(description="The question to ask the user.")
    header: str = Field(default="question", description="Short header (<=30 chars).")
    options: List[QuestionOption] = Field(
        default_factory=list, description="Selectable options. Put the recommended first."
    )
    multiple: Optional[bool] = Field(default=False, description="Allow multiple selections.")
    custom: Optional[bool] = Field(default=True, description="Allow free-text answers.")


class QuestionParams(BaseModel):
    questions: List[QuestionPrompt] = Field(description="The questions to ask the user.")


class QuestionTool:
    name = "question"
    description = (
        "Ask the user a question and wait for their reply. Use this when required "
        "inputs are incomplete: list what is still missing, then ask whether they "
        "have any other information to provide. Offer options to supply more fields "
        "or to continue the analysis without them. Do not invent missing values. "
        "Put the recommended option first and append (Recommended) to it."
    )
    params = QuestionParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        prompts = parse_question_prompts(args)
        answers = ctx.ask_question(prompts)
        return ToolResult.success(
            "question",
            format_question_result(prompts, answers),
            answers=[list(a) for a in answers],
        )


def parse_question_prompts(args: dict) -> List[dict]:
    p = QuestionParams(**(args or {}))
    return [q.model_dump() for q in p.questions]


def format_question_result(questions: List[dict], answers: List[List[str]]) -> str:
    formatted = ", ".join(
        f'"{q.get("question")}"="{", ".join(ans) if ans else "Unanswered"}"'
        for q, ans in zip(questions, answers)
    )
    return (
        f"User has answered your questions: {formatted}. "
        "If they provided more information, use it. "
        "If they said there is nothing more to add, continue the analysis "
        "with the information already collected; do not invent missing values."
    )
