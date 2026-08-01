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
        "Ask the user a question during execution — for preferences, "
        "clarifications, or decisions the model cannot make alone. Blocks "
        "until the user answers. Put the recommended option first and append "
        "(Recommended) to it."
    )
    params = QuestionParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = QuestionParams(**args)
        prompts = [q.model_dump() for q in p.questions]
        answers = ctx.ask_question(prompts)
        formatted = ", ".join(
            f'"{q.question}"="{", ".join(ans) if ans else "Unanswered"}"'
            for q, ans in zip(p.questions, answers)
        )
        return ToolResult.success(
            "question",
            f"User has answered your questions: {formatted}. Continue with the user's answers in mind.",
            answers=[list(a) for a in answers],
        )
