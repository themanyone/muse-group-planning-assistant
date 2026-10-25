"""Submission pack coherence (roadmap B-150).

The pack in submission/ is the cold reader's door to this
backend: the model mapping, the architecture, the run
instructions, and the scope/waiver note. Its claims are only
worth shipping if they cannot drift from the facts they
summarise, so — the B-139 discipline — this file PARSES the
pack: the documents must exist, the decided licence must be
stated in its ruled words, the waiver must name the
pre-existing projects it excludes and the one carve-out it
allows, the model page must carry its honesty sentence, and
no document may resurrect the superseded licence posture the
project ruled against. A pack that drifts fails here, before
any human reads it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "submission"

DOCS = (
    "README.md",
    "MODEL_MAPPING.md",
    "ARCHITECTURE.md",
    "RUN.md",
    "WAIVER_AND_SCOPE.md",
)

LICENCE_LINE = "Licence: GNU General Public License version 2 (GPLv2)."


def _read(name: str) -> str:
    return (PACK / name).read_text(encoding="utf-8")


def test_pack_documents_exist():
    missing = [name for name in DOCS if not (PACK / name).is_file()]
    assert missing == [], f"submission pack is missing documents: {missing}"


def test_licence_is_stated_in_its_ruled_words():
    # The licence was decided by ruling (GPLv2); the cover and
    # the waiver both carry the statement verbatim, the same
    # words the repository README carries.
    for name in ("README.md", "WAIVER_AND_SCOPE.md"):
        assert LICENCE_LINE in _read(name), f"{name} must state the licence verbatim"


def test_no_superseded_licence_language_anywhere_in_the_pack():
    # The roadmap's B-150 draft text named a licence notice
    # that the owner's ruling superseded before this slice was
    # built. The pack must not resurrect that posture, even in
    # passing — a cold reader takes every sentence at face
    # value.
    banned = ("undecided", "to be decided", "all rights reserved")
    offenders = []
    for name in DOCS:
        text = _read(name).lower()
        for phrase in banned:
            if phrase in text:
                offenders.append(f"{name}: {phrase!r}")
    assert offenders == [], f"superseded licence language in the pack: {offenders}"


def test_waiver_names_the_pre_existing_projects_and_the_one_carve_out():
    text = _read("WAIVER_AND_SCOPE.md")
    for project in ("Samtal", "Mythic Engineering", "Project Aesir"):
        assert project in text, f"waiver must name {project} as not submitted"
    # The carve-out is narrow and named: the Phase 11 Samtal
    # integration surface, and only as it exists in this repo.
    assert "Phase 11" in text
    assert "integration surface" in text


def test_model_mapping_is_honest_about_never_running_live():
    text = _read("MODEL_MAPPING.md")
    # The three exercised driver modes, the two tiers by their
    # configuration names, and the sentence the page leads
    # with: no real endpoint exists in this environment.
    for needle in ("fixture", "mock", "live", "GPA_AI_MODEL_SPARK", "GPA_AI_MODEL_WONDER"):
        assert needle in text, f"model mapping must name {needle}"
    assert "no real Meta model endpoint" in text


def test_model_mapping_matches_the_shipped_defaults():
    # The tier default ids the pack quotes must be the ones the
    # shipped .env.example actually carries — the mapping is a
    # description of the build, not a wish.
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    text = _read("MODEL_MAPPING.md")
    for var in ("GPA_AI_MODEL_SPARK", "GPA_AI_MODEL_WONDER"):
        match = re.search(rf"^{var}=(\S+)", env_example, re.MULTILINE)
        assert match, f"{var} missing from .env.example"
        assert match.group(1) in text, f"model mapping must quote the shipped default for {var}"


def test_architecture_carries_the_diagram_and_the_tour():
    text = _read("ARCHITECTURE.md")
    assert "```mermaid" in text, "the architecture page must carry its diagram"
    # The walking tour names the real layers of the codebase.
    for layer in ("routers", "services", "MetaModelClient", "WAL"):
        assert layer in text, f"architecture tour must walk {layer}"


def test_cover_and_run_point_at_the_evidence():
    cover = _read("README.md")
    for needle in ("RESULTS.md", "TEST_REPORT.md", "FREEZE_DRILL.md"):
        assert needle in cover, f"the cover must point at {needle}"
    run = _read("RUN.md")
    for needle in ("uv sync", "demo.sh", "test_phase5_gate.py", "test_phase7_gate.py", "test_demo_flows.py"):
        assert needle in run, f"run instructions must name {needle}"
    # The four flow tests the run page cites must exist — the
    # script is only a script if its receipts are real files.
    for test_file in ("test_phase5_gate.py", "test_phase7_gate.py", "test_demo_flows.py"):
        assert (ROOT / "tests" / test_file).is_file(), f"missing flow test {test_file}"
