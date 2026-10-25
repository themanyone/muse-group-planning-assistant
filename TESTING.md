# Testing Ethos
The Group Planning Assistant (GPA) is built on the principle of **Mythic Engineering**: systems must be robust, predictable, and resilient to the chaos of the real world. Our testing philosophy reflects this.

## Core Principles
- **Drafts First**: We never assume a side effect happened. We verify the *intent* (the draft/job/row) before we verify the *action* (the email/notification).
- **Resilience over Optimism**: We don't just test the "happy path." We actively simulate failing mail servers, expired tokens, and malformed AI extractions to ensure the system fails gracefully and recovers predictably.
- **Equality of Standing**: AI agents are treated as first-class citizens. If a human can't do it, an agent shouldn't either. All agentic workflows are subject to the same rigorous integration testing.
- **Evidence-Based Verification**: We don't just check status codes. We inspect the database, the outbox, and the transport logs to ensure the system's internal state matches the external reality.

# Testing Planning
Testing is not a post-development phase; it is a continuous, phase-aligned activity.
- **Phase-Aligned Gates**: Each development phase (e.g., Phase 8: Notifications) requires a corresponding suite of "gate" tests to ensure new functionality doesn't regress existing capabilities.
- **AI-First Validation**: As an AI-native application, our testing includes dedicated evaluation pipelines for LLM extraction and Vision models, treating model performance as a core metric.

# Criteria for Success
A feature is considered "Done" only when it meets the following:
- **Functional Correctness**: All `pytest` suites pass with zero failures.
- **Security Integrity**: No sensitive data (passwords, hashes, private tokens) is ever leaked in API responses or logs.
- **AI Precision**: Extraction precision and recall meet the defined thresholds for the current phase.
- **Migration Safety**: All database migrations are verified via up/down/up cycles.

# Methodology
We employ a multi-layered testing strategy:
1. **Unit & Integration Tests**: Using `pytest` and `FastAPI TestClient` to validate business logic and API endpoints.
2. **Simulation Drivers**: Custom drivers (e.g., `CaptureTransport`, `FailingTransport`) to simulate external service behavior without actual network calls.
3. **AI Evaluation**: Specialized scripts (`gpa.ai.evaluation`) to run large corpora through models and compare against "gold" datasets.
4. **Vision Evaluation**: Dedicated pipelines to measure model accuracy on visual inputs (handwriting, rotation, etc.).

# Status & Recommendations
**Current Status**: `ZIP-Beta (v0.3)` - Phase 8 (Notifications & Reminders).
- **Successes**: High reliability in notification queuing and job processing.
- **Critical Gaps**: AI extraction for `event:cost` and `event:place` requires significant refinement to meet production-grade precision.
- **Next Steps**: Focus on refining the extraction prompts and expanding the vision evaluation corpus.
