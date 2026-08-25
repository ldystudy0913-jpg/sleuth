"""Permission system — opencode port.

opencode's permission model (packages/schema/src/v1/permission.ts,
packages/opencode/src/permission/index.ts, packages/core/src/util/wildcard.ts):

  Rule     = { permission: str, pattern: str, action: "allow"|"deny"|"ask" }
  Ruleset  = Rule[]

  evaluate(permission, pattern, *rulesets):
      flatten all rulesets, find the LAST rule where
      Wildcard.match(permission, rule.permission)
          AND Wildcard.match(pattern, rule.pattern);
      if none matches -> default { action: "ask", pattern: "*" }

  ask(request):  for each pattern -> evaluate(ruleset + approved);
                 deny -> DeniedError; allow -> continue; ask -> block for reply
  reply:         "once" (approve once) | "always" (append approved rules for
                 the patterns in request.always) | "reject" (cascade-reject)

  fromConfig:    { bash: "ask" }                              -> [{bash, *, ask}]
                 { bash: { "git *": "allow" } }               -> [{bash, git *, allow}]
                 with `~` / `$HOME` expansion on patterns
  visibleTools:  a tool with a `deny *` rule is hidden from the model

This module ports that logic to Python. The blocking `ask` is synchronous:
the supplied `ask_fn` is called and returns a Reply; `always` appends to the
in-memory approved list so subsequent matching patterns auto-allow. There is
NO tenant dimension — opencode scopes approvals to project/session only.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

Action = str  # "allow" | "deny" | "ask"
Reply = str  # "once" | "always" | "reject"


@dataclass
class Rule:
    permission: str
    pattern: str
    action: Action

    def matches(self, permission: str, pattern: str) -> bool:
        return wildcard_match(permission, self.permission) and wildcard_match(pattern, self.pattern)


Ruleset = List[Rule]


# ---------------------------------------------------------------------------
# wildcard matching — port of opencode's packages/core/src/util/wildcard.ts
# ---------------------------------------------------------------------------

def _compile_wildcard(pattern: str) -> re.Pattern:
    normalized = pattern.replace("\\", "/")
    escaped = re.sub(r"[.+^${}()|[\]\\]", lambda m: "\\" + m.group(), normalized)
    escaped = escaped.replace("*", ".*").replace("?", ".")
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    flags = re.IGNORECASE | re.DOTALL if sys.platform == "win32" else re.DOTALL
    return re.compile("^" + escaped + "$", flags)


_MATCH_CACHE: Dict[str, re.Pattern] = {}


def wildcard_match(value: str, pattern: str) -> bool:
    """Glob match: `*` -> any sequence, `?` -> one char; full match.

    Case-insensitive on Windows (matches opencode). Backslashes normalise to
    forward slashes. Results are cached per pattern.
    """
    regex = _MATCH_CACHE.get(pattern)
    if regex is None:
        regex = _compile_wildcard(pattern)
        _MATCH_CACHE[pattern] = regex
    return regex.match(value.replace("\\", "/")) is not None


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def evaluate(permission: str, pattern: str, *rulesets: Ruleset) -> Rule:
    """Last matching rule wins; default is ask. Port of opencode evaluate()."""
    flat: List[Rule] = []
    for rs in rulesets:
        flat.extend(rs)
    for rule in reversed(flat):
        if rule.matches(permission, pattern):
            return rule
    return Rule(permission=permission, pattern="*", action="ask")


# ---------------------------------------------------------------------------
# config -> ruleset
# ---------------------------------------------------------------------------

def _expand(pattern: str) -> str:
    home = os.path.expanduser("~")
    if pattern.startswith("~/"):
        return home + pattern[1:]
    if pattern == "~":
        return home
    if pattern.startswith("$HOME/"):
        return home + pattern[5:]
    if pattern.startswith("$HOME"):
        return home + pattern[5:]
    return pattern


def from_config(permission: Dict[str, object]) -> Ruleset:
    """Convert the opencode.json `permission` block into a Ruleset.

    Accepts either a flat Action (applies to all) or {tool: Action | {pattern: Action}}.
    """
    rules: Ruleset = []
    for key, value in permission.items():
        if isinstance(value, str):
            rules.append(Rule(permission=key, action=value, pattern="*"))
            continue
        if isinstance(value, dict):
            for pat, act in value.items():
                rules.append(Rule(permission=key, pattern=_expand(pat), action=act))
    return rules


# ---------------------------------------------------------------------------
# tool visibility — hide tools whose permission is `deny *`
# ---------------------------------------------------------------------------

def disabled_tools(tool_names: Sequence[str], ruleset: Ruleset) -> set:
    """Tools with a `deny` rule whose pattern is `*` are removed from the model."""
    out = set()
    for name in tool_names:
        rule = None
        for r in reversed(ruleset):
            if wildcard_match(name, r.permission):
                rule = r
                break
        if rule is not None and rule.pattern == "*" and rule.action == "deny":
            out.add(name)
    return out


def visible_tools(tools, ruleset: Ruleset):
    """Return only the tools the model is allowed to see. `tools` is a name->obj dict."""
    hidden = disabled_tools(list(tools), ruleset)
    return {name: obj for name, obj in tools.items() if name not in hidden}


# ---------------------------------------------------------------------------
# Rule baselines per agent (replaces the old simple Ruleset builders)
# ---------------------------------------------------------------------------

def build_rules() -> Ruleset:
    """Default interactive build mode: confirm edits & bash."""
    return [
        Rule("read", "*", "allow"),
        Rule("glob", "*", "allow"),
        Rule("grep", "*", "allow"),
        Rule("write", "*", "ask"),
        Rule("edit", "*", "ask"),
        Rule("bash", "*", "ask"),
        Rule("todo", "*", "allow"),
        Rule("question", "*", "allow"),
        Rule("webfetch", "*", "ask"),
        Rule("task", "*", "ask"),
        Rule("kb_lookup", "*", "allow"),
        Rule("save_output_file", "*", "allow"),
        Rule("read_session_file", "*", "allow"),
        Rule("memory_search", "*", "allow"),
        Rule("memory_write", "*", "allow"),
        Rule("memory_forget", "*", "allow"),
        Rule("ddreply_*", "*", "allow"),
    ]


def plan_rules() -> Ruleset:
    """Read-only: edits/writes denied, bash must be confirmed."""
    return [
        Rule("read", "*", "allow"),
        Rule("glob", "*", "allow"),
        Rule("grep", "*", "allow"),
        Rule("write", "*", "deny"),
        Rule("edit", "*", "deny"),
        Rule("bash", "*", "ask"),
        Rule("todo", "*", "allow"),
        Rule("question", "*", "allow"),
        Rule("webfetch", "*", "ask"),
        Rule("task", "*", "deny"),
        Rule("kb_lookup", "*", "allow"),
        Rule("save_output_file", "*", "deny"),
        Rule("read_session_file", "*", "allow"),
        Rule("memory_search", "*", "allow"),
        Rule("memory_write", "*", "ask"),
        Rule("memory_forget", "*", "ask"),
    ]


def allow_all_rules() -> Ruleset:
    return [Rule("*", "*", "allow")]


# ---------------------------------------------------------------------------
# Permission service
# ---------------------------------------------------------------------------

class PermissionDenied(Exception):
    """A tool action was denied by a rule or rejected by the user."""


# An ask callback: (permission, patterns, always) -> (Reply, feedback?)
AskFn = Callable[[str, List[str], List[str]], tuple]


def _console_ask(permission: str, patterns: List[str], always: List[str]) -> tuple:
    """Default interactive prompt: once / always / reject."""
    detail = ", ".join(patterns[:3])
    extra = f"  [a] always-allow {', '.join(always[:2])}\n" if always else ""
    prompt = (
        f"\n[permission] {permission}: {detail}\n"
        f"  [o] once\n{extra}"
        f"  [r] reject\n> "
    )
    try:
        ans = input(prompt).strip().lower() or "o"
    except (EOFError, KeyboardInterrupt):
        return "reject", "interrupted"
    if ans in ("a", "always") and always:
        return "always", None
    if ans in ("r", "reject", "n", "no"):
        return "reject", None
    return "once", None


@dataclass
class Permission:
    """Per-session permission gate with an in-memory approved list.

    Mirrors opencode's Permission service: `ask()` evaluates the agent ruleset
    plus accumulated approvals; on "always" replies the matched patterns are
    appended as allow rules (so later calls auto-allow, like opencode's
    cascading auto-approval of same-session pending requests).
    """

    rules: Ruleset = field(default_factory=build_rules)
    approved: Ruleset = field(default_factory=list)
    ask_fn: AskFn = field(default=_console_ask)

    def evaluate(self, permission: str, pattern: str) -> Rule:
        return evaluate(permission, pattern, self.approved, self.rules)

    def ask(self, permission: str, patterns: List[str], always: Optional[List[str]] = None) -> None:
        """Gate a tool action. Raises PermissionDenied if denied/rejected."""
        always = always or []
        needs_ask = False
        for pat in patterns:
            rule = self.evaluate(permission, pat)
            if rule.action == "deny":
                raise PermissionDenied(f"{permission} is denied by rule {rule.pattern}")
            if rule.action == "allow":
                continue
            needs_ask = True
        if not needs_ask:
            return
        reply, feedback = self.ask_fn(permission, patterns, always)
        if reply == "reject":
            raise PermissionDenied(f"{permission} rejected by user" + (f": {feedback}" if feedback else ""))
        if reply == "always":
            for pat in always:
                self.approved.append(Rule(permission=permission, pattern=pat, action="allow"))
        # "once" -> nothing persisted
        return

    def check(self, tool: str, detail: str = "") -> None:
        """Compatibility shim for the old single-arg API. Prefer ask()."""
        self.ask(tool, [detail or "*"], [])
