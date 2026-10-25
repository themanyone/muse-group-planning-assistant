"""The versioned prompt library (roadmap B-038).

Prompts are *code-adjacent artefacts*, versioned like code: each
template lives in ``ai/prompts/<name>.md`` with a front-matter
header (``name`` / ``version``) and its output JSON Schema beside
it in ``<name>.schema.json``. The prompt's version is stored on
every candidate it produces, so any draft can answer "which
instructions made you?".

The guard that makes the versioning real is ``manifest.json``: it
records, per prompt, the current version and the SHA-256 of the
template file. Loading a prompt verifies both — a template edited
without bumping its version (and updating the manifest) no longer
matches its recorded hash and **fails loudly at load**, which is
the roadmap's "changing a prompt without bumping its version
fails CI" acceptance, enforced in the test suite and at runtime
alike. To change a prompt: edit it, bump the version in its
header, and regenerate the manifest
(``python -m gpa.ai.promptlib --write-manifest``).

Placeholders in template bodies are ``{{NAME}}`` tokens, replaced
literally at render time — no format-string machinery, so a
template may contain any braces its JSON examples need.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class PromptError(Exception):
    """A prompt is missing, malformed, or fails verification."""


@dataclass
class PromptTemplate:
    """One loaded, verified template + its output schema."""

    name: str
    version: int
    body: str
    schema: dict | None

    def render(self, **variables: str) -> str:
        """Substitute {{NAME}} placeholders with the given words."""
        text = self.body
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", value)
        return text


def _parse_template(text: str, *, name: str) -> tuple[int, str]:
    """Split a template file into (version, body) via front matter."""
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise PromptError(f"Prompt '{name}' has no front-matter header.")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    if meta.get("name") != name:
        raise PromptError(f"Prompt file '{name}.md' declares name '{meta.get('name')}'.")
    try:
        version = int(meta["version"])
    except (KeyError, ValueError) as exc:
        raise PromptError(f"Prompt '{name}' has no integer version.") from exc
    return version, text[match.end():]


def _manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise PromptError("The prompt manifest.json is missing.")
    return json.loads(path.read_text(encoding="utf-8"))["prompts"]


def verify_manifest(root: Path | None = None) -> list[str]:
    """Check every template against the manifest; return problems.

    A template whose bytes changed without a version bump (and a
    manifest refresh) shows up here as a hash mismatch; a version
    bumped in the header but not the manifest shows up as a
    version mismatch. An empty list means the library is sound.
    """
    root = root or PROMPTS_DIR
    problems: list[str] = []
    try:
        manifest = _manifest(root)
    except PromptError as exc:
        return [str(exc)]
    seen: set[str] = set()
    for path in sorted(root.glob("*.md")):
        name = path.stem
        seen.add(name)
        text = path.read_text(encoding="utf-8")
        try:
            version, _body = _parse_template(text, name=name)
        except PromptError as exc:
            problems.append(str(exc))
            continue
        entry = manifest.get(name)
        if entry is None:
            problems.append(f"Prompt '{name}' is not in the manifest.")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            problems.append(
                f"Prompt '{name}' changed without a version bump "
                "(template hash no longer matches the manifest)."
            )
        if version != entry["version"]:
            problems.append(
                f"Prompt '{name}' is version {version} in its header "
                f"but {entry['version']} in the manifest."
            )
    for name in manifest:
        if name not in seen:
            problems.append(f"Manifest names prompt '{name}', which has no template file.")
    return problems


def write_manifest(root: Path | None = None) -> Path:
    """Regenerate manifest.json from the templates on disk.

    The deliberate act that accompanies a version bump (see the
    module docstring): hashes are recomputed from the files as
    they stand, versions from their headers.
    """
    root = root or PROMPTS_DIR
    prompts: dict[str, dict] = {}
    for path in sorted(root.glob("*.md")):
        name = path.stem
        version, _body = _parse_template(path.read_text(encoding="utf-8"), name=name)
        prompts[name] = {
            "version": version,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    out = root / "manifest.json"
    out.write_text(
        json.dumps({"prompts": prompts}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out


@lru_cache(maxsize=None)
def _load_cached(name: str, root_str: str) -> PromptTemplate:
    root = Path(root_str)
    path = root / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"There is no prompt named '{name}'.")
    problems = [p for p in verify_manifest(root) if f"'{name}'" in p]
    if problems:
        raise PromptError("; ".join(problems))
    version, body = _parse_template(path.read_text(encoding="utf-8"), name=name)
    schema = None
    schema_path = root / f"{name}.schema.json"
    if schema_path.is_file():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return PromptTemplate(name=name, version=version, body=body, schema=schema)


def load_prompt(name: str, *, root: Path | None = None) -> PromptTemplate:
    """Load (and verify) one prompt template by name."""
    return _load_cached(name, str(root or PROMPTS_DIR))


def prompt_version(name: str) -> int:
    """The current version of a prompt — what candidates record."""
    return load_prompt(name).version


def render_prompt(name: str, **variables: str) -> str:
    """Render one template's body with its placeholders filled."""
    return load_prompt(name).render(**variables)


if __name__ == "__main__":
    # `python -m gpa.ai.promptlib --write-manifest` after a bump;
    # with no arguments, verify and report (used by humans; the
    # test suite calls verify_manifest directly).
    import sys

    if "--write-manifest" in sys.argv:
        print(f"Wrote {write_manifest()}")
    else:
        found = verify_manifest()
        print("Prompt library sound." if not found else "\n".join(found))
