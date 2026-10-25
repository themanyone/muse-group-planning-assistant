# AGENTS.md

This document provides essential context and guidelines for AI agents working within the Group Planning Assistant (GPA) codebase.

## 🚀 Essential Commands

### Development & Environment
- **Dependency Management**: Uses `uv`.
- **Running the App**: `bash run.sh`
- **Running Tests**: `bash test.sh`

### Database & Migrations
- **Migrations**: Uses `alembic`.
- **Seeding**: `python backend/scripts/seed.py`

### Code Generation & Utilities
- **OpenAPI Export**: `python backend/scripts/export_openapi.py`
- **TypeScript Type Generation**: `python backend/scripts/generate_ts_types.py`

## 🏗️ Architecture & Data Flow

### Core System
The GPA is a Python backend (FastAPI) consisting of several cooperating processes:
- **API Process**: Serves REST, SSE, and MCP.
- **MCP Server**: The primary interface for AI agents (via stdio or HTTP).
- **Job Worker**: Handles long-running tasks (AI pipelines, email, push notifications).
- **Scheduler**: A 30-second tick scheduler that materializes reminders and digests as `Job` rows.

### Communication: The Internal API Bus
**Crucial**: No module communicates with another directly. All communication must pass through the **internal encrypted API bus**. This ensures that every action is authenticated, authorized, and audited.

### The "Drafts First" Workflow
The system is designed around a **review queue**. Most state-changing operations do not modify the primary state immediately. Instead, they create a **draft** or a **proposal** (e.g., `events.propose_change`). A human (or an authorized agent) must then approve the draft in the queue before it is materialized into the system's permanent state.

## 🛠️ Coding Standards & Patterns

### General Python
- **Style**: Follow **PEP 8** (4-space indents, `snake_case` for variables/functions, `CamelCase` for classes).
- **Typing**: Use **type hints** extensively.
- **Error Handling**: Wrap subsystems in `try/except` blocks for fault tolerance. Use the comprehensive logger; **never use `print()`**.
- **Modularity**: Keep methods focused (one responsibility, ideally < 50 lines).
- **Location Agnostic**: **Never use absolute paths**. The codebase must be portable.

### AI & Prompts
- **Prompt Storage**: Prompts are stored as `.md` files in `backend/src/gpa/ai/prompts/`.
- **Schemas**: Every prompt file should have a corresponding `.schema.json` file defining its expected input/output structure.

### Data & State
- **Immutability**: Base data files (YAML/JSON) are **never** modified. All session-specific changes are stored in the session layer.
- **Separation of Concerns**: Keep static knowledge (lore, profiles, charts) in `data/` files and reasoning logic in Python.

## 🤖 Agent-Specific Context

### MCP Interface
- **Tool Naming**: Tools follow the `domain.verb_noun` pattern (e.g., `groups.read`, `sources.submit_text`).
- **Equality of Standing**: AI agents are "equal principals." They use the same operations as humans and are subject to the same authorization and approval laws.
- **Authorization**: Scope is re-checked **at call time**, not just at connection time. A revoked key or narrowed scope takes effect immediately.
- **No Bulk-Approve**: There is no tool to approve all pending items at once.

### Testing
- Comprehensive tests are located in `backend/tests/`.
- Tests cover everything from authentication to complex AI extraction pipelines.

## ⚠️ Important Gotchas
- **No Pseudocode**: Never write pseudocode in `.py` files. If you need to plan, write a `.md` file first.
- **No Hardcoding**: Never hardcode settings, data, or lore in Python. Always load from data files.
- **Drafts are Default**: If you are unsure if an action is state-changing, assume it creates a draft/proposal.
