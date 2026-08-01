"""sleuth — a Python reimplementation of opencode's core coding-agent loop.

This package ports the essential architecture of opencode (the open source
AI coding agent, https://opencode.ai) to Python:

* a provider-agnostic message model and streaming event protocol
* a tool registry with pydantic-validated tools (read/write/edit/bash/...)
* an agentic session loop that drives the model until it stops calling tools
* a layered config system (project + global) inspired by opencode.json

It is NOT affiliated with the opencode team. See README.md.
"""

__version__ = "0.1.0"
