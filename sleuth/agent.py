"""Agent definitions.

opencode ships built-in agents (`build`, `plan`, `general`, `explore`, ...)
that differ in their tools, permissions, and system prompt. Here we expose a
registry mapping agent name -> permission ruleset baseline.

Custom agents from `.opencode/agent/*.md` are merged via config; this module
holds the built-in permission baselines (opencode `agent/agent.ts`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set

from .permission import Rule, Ruleset, build_rules, plan_rules

if TYPE_CHECKING:
    from .config import Config

# Port of opencode explore: deny-all then allow read-only exploration tools
_EXPLORE_RULES: Ruleset = [
    Rule("*", "*", "deny"),
    Rule("grep", "*", "allow"),
    Rule("glob", "*", "allow"),
    Rule("bash", "*", "allow"),
    Rule("webfetch", "*", "allow"),
    Rule("read", "*", "allow"),
    Rule("question", "*", "allow"),
]

_GENERAL_RULES: Ruleset = build_rules() + [
    Rule("todo", "*", "deny"),
    Rule("webfetch", "*", "allow"),
    Rule("task", "*", "deny"),  # nested task denied unless overridden
]


BUILTIN: Dict[str, Ruleset] = {
    "build": build_rules(),
    "plan": plan_rules(),
    "general": _GENERAL_RULES,
    "explore": _EXPLORE_RULES,
}


def ruleset_for(name: str) -> Ruleset:
    """Return the permission-rule baseline for an agent (list of Rule)."""
    return list(BUILTIN.get(name, BUILTIN["build"]))


def known_agents(config: Optional["Config"] = None) -> Set[str]:
    """Names valid for `--agent` / task `subagent_type`."""
    names = set(BUILTIN)
    if config is not None:
        names |= set(config.agents)
    # hide internal agents
    names -= {"title", "compaction", "summary"}
    return names


def list_primary_agents(config: "Config") -> List[str]:
    """Agents intended for CLI `--agent` (primary / all, not hidden)."""
    out = []
    for name in sorted(known_agents(config)):
        acfg = config.agent(name)
        if acfg.hidden:
            continue
        if acfg.mode == "subagent" and name not in ("build", "plan"):
            # still list custom primaries; built-in subagents stay task-only
            if name in ("general", "explore"):
                continue
        out.append(name)
    # always include build/plan
    for n in ("build", "plan"):
        if n not in out:
            out.insert(0, n)
    return out
