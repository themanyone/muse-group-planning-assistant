# Frontend Strategy: Muse GPA Hackathon Expansion

This document models the frontend architectural requirements and UI/UX patterns needed to support the proposed Muse GPA features for the Meta AI Developer Hackathon.

## 🎯 Design Philosophy: "The Human in the Loop"

The frontend must reinforce the core Muse GPA principle: **The Machine Drafts, The Human Decides.** Every AI-driven capability must be presented as a *proposal* or a *draft* that requires explicit human verification before becoming part of the group's permanent state.

---

## 🛠️ Feature-Specific UI Modules

### 1. The "Vision Review" Workflow (Physical-to-Digital Bridge)
*Leveraging SAM 3.1 and Muse Image for high-fidelity segmentation and extraction.*

*   **Capture Interface**:
    *   **Mobile-First Camera**: A dedicated camera module with real-time guidance (e.g., "Align flyer within the frame").
    *   **Multi-Shot Mode**: Ability to take multiple photos of a large noticeboard to build a composite view.
*   **Segmentation Overlay (The "Wall" View)**:
    *   **Interactive Bounding Boxes**: Using `region_json` from the backend, the UI renders interactive SVG/Canvas overlays on the image.
    *   **Tap-to-Inspect**: Tapping a segmented region (a flyer or poster) opens a side-panel for review.
*   **The "Drafting" Side-Panel**:
    *   **Split-Screen Review**: A side-by-side view: the original image (left) and a structured form (right).
    *   **Visual Provenance**: When a user focuses on a field (e.g., "Event Date"), the corresponding region in the image is highlighted/pulsed.
    *   **Confidence Indicators**: Visual cues (e.g., color-coded borders) indicating the AI's confidence in the extraction.

### 2. The "Multi-Modal Ingest" Hub (Advanced Extraction)
*Leveraging Muse Spark/Wonder for complex text and multi-modal reasoning.*

*   **The "Messy Input" Modal**: A unified interface for:
    *   **Text Paste**: A large text area for pasting chat logs (WhatsApp, Discord, etc.).
    *   **File Drop**: Drag-and-drop for PDFs, HTML, or images.
    *   **Voice Recording**: A waveform-based recorder for voice notes.
*   **The "Extraction Preview"**:
    *   Instead of immediate ingestion, the UI presents a "Proposed Changes" list.
    *   **Conflict Detection**: If an extracted event conflicts with an existing one, the UI highlights the conflict in red and suggests a resolution.

### 3. The "Agentic Social Dashboard" (Agentic Coordination)
*Leveraging Muse MCP and advanced reasoning models for autonomous group management.*

*   **The "Muse Activity" Feed**:
    *   A real-time, non-intrusive stream of agent actions (e.g., *"Muse is analyzing the group's availability for next Friday..."*).
    *   Uses SSE (Server-Sent Events) to ensure the UI feels alive and responsive to agent "thoughts."
*   **Interactive Proposal Cards**:
    *   The agent doesn't just "do" things; it "proposes" them.
    *   **Proposal UI**: A card-based interface for agent-initiated actions:
        *   *Example*: "Muse proposes: Move 'Hiking Trip' to Sunday to avoid rain. [Approve] [Reject] [Edit]".
*   **The "Agent Command" Interface**:
    *   A natural language command bar (similar to a search bar) that allows users to issue direct instructions: *"Muse, find a venue for the next meeting."*

---

## 🏗️ Technical Implementation Requirements

### 1. Real-Time Communication
*   **SSE (Server-Sent Events)**: Essential for the "Agent Activity Feed" and real-time status updates on long-running extraction jobs.
*   **Optimistic UI**: For user-initiated actions (like approving a draft), use optimistic updates to ensure the interface feels instantaneous.

### 2. Visual Rendering & Provenance
*   **Canvas/SVG Layering**: A robust system to layer AI-generated bounding boxes and highlights over user-uploaded images.
*   **Coordinate Mapping**: A utility to map backend `region_json` coordinates (normalized) to the actual rendered dimensions of the image in the browser.

### 3. State Management
*   **Draft vs. Committed**: A strict distinction in the frontend state between `Draft` objects (pending review) and `Committed` objects (permanent group state).
*   **Role-Based Visibility**: The UI must dynamically adapt based on the user's role (Guest vs. Member vs. Organiser), as defined by the backend's role-based projections.

---
*Prepared for the Muse GPA Hackathon Expansion Strategy.*
