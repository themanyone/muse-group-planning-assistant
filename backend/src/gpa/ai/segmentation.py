"""Board segmentation — the driver boundary (roadmap B-064, D-19).

Cutting a noticeboard photograph into its posters is the ONE job
segmentation does here (D-19: SAM is used only for this
separation; the reading of each poster is the vision pipeline's,
ai/vision.py). Like the model client, segmentation sits behind a
small protocol with interchangeable drivers, because the roadmap
decided the fallback in advance:

- ``SamSegmenter`` — a SAM 3.1-shaped HTTP endpoint (POST the
  image, receive region boxes). It exists as a real wire client,
  stub-transport tested like the live model driver (B-037); with
  no endpoint configured it raises ``SegmenterUnavailable`` in
  plain words — it never pretends to have cut anything.
- ``VisionBoxSegmenter`` — the decided fallback: the vision model
  (through the ordinary MetaModelClient) is asked for the poster
  boxes, and the SAME region pipeline runs on its answer. The
  demo therefore never depends on one model's paperwork.

``build_segmenter`` picks SAM when settings name an endpoint and
the vision fallback otherwise; the board service also falls back
at run time if a configured SAM endpoint refuses — a board that
cannot be SAM-cut is still read, and the audit says which driver
did the cutting.

Every box, from every driver, passes ``valid_region`` before it
may become a child flyer: an off-picture or degenerate box is
dropped, never repaired into a guess.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from gpa.ai import promptlib
from gpa.ai.client import MetaModelClient, ModelError
from gpa.ai.vision import valid_region

PROMPT_NAME = "board_segmentation"


class SegmenterUnavailable(ModelError):
    """This segmenter cannot run here (no endpoint configured).

    A configuration fact, not a failure of the photograph: the
    board service answers it with the fallback driver, and says
    so in the audit trail."""


@dataclass
class Region:
    """One poster's rectangle on the board: [x, y, w, h] as
    fractions of the whole image, plus the driver's own label for
    it (a hint for reviewers, never a reading)."""

    box: list[float]
    label: str = ""


@runtime_checkable
class BoardSegmenter(Protocol):
    """The protocol every segmentation driver implements."""

    driver_name: str

    def segment(self, *, image_bytes: bytes) -> list[Region]:
        """Cut one board photograph into its poster regions."""
        ...


def _clean_regions(raw: object) -> list[Region]:
    """Validate a driver's raw region list: keep only honest
    boxes, as float lists, in the driver's own order."""
    regions: list[Region] = []
    if not isinstance(raw, list):
        return regions
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        box = entry.get("box")
        if valid_region(box):
            regions.append(
                Region(
                    box=[float(v) for v in box],
                    label=str(entry.get("label") or "")[:80],
                )
            )
    return regions


class VisionBoxSegmenter:
    """The fallback driver: poster boxes from the vision model.

    One read_image call against the board_segmentation prompt;
    the answer's regions pass the same validation SAM's would.
    """

    driver_name = "vision"

    def __init__(self, client: MetaModelClient):
        self._client = client

    def segment(self, *, image_bytes: bytes) -> list[Region]:
        template = promptlib.load_prompt(PROMPT_NAME)
        prompt = promptlib.render_prompt(PROMPT_NAME)
        result = self._client.read_image(
            prompt=prompt, image_bytes=image_bytes, schema=template.schema or {}
        )
        data = result.data
        if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
            raise ModelError(
                "Board segmentation answered in an unexpected shape: "
                "no 'regions' list."
            )
        return _clean_regions(data["regions"])


class SamSegmenter:
    """A SAM-shaped segmentation endpoint, over plain httpx.

    The wire contract is deliberately tiny and stated here so an
    operator can point GPA_SAM_API_BASE at any service honouring
    it: POST the image bytes to ``{base}/segment`` (Bearer key
    when one is configured); receive
    ``{"regions": [{"box": [x, y, w, h], "label": ...}]}``.
    Tests drive it through a stubbed transport — the suite never
    touches a real endpoint, and with no base URL the driver is
    unavailable by construction.
    """

    driver_name = "sam"

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._base = (base_url or "").rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def segment(self, *, image_bytes: bytes) -> list[Region]:
        if not self._base:
            raise SegmenterUnavailable(
                "No SAM endpoint is configured (GPA_SAM_API_BASE is empty) — "
                "the vision fallback will cut this board instead."
            )
        headers = {"Content-Type": "application/octet-stream"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base}/segment", headers=headers, content=image_bytes
                )
        except httpx.HTTPError as exc:
            raise ModelError(f"SAM endpoint unreachable: {exc}.") from exc
        if response.status_code >= 400:
            raise ModelError(
                f"SAM endpoint refused the board: HTTP {response.status_code}."
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("SAM endpoint answered with a non-JSON body.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
            raise ModelError(
                "SAM endpoint answered in an unexpected shape: no 'regions' list."
            )
        return _clean_regions(data["regions"])


def build_segmenter(settings, client: MetaModelClient) -> BoardSegmenter:
    """The configured segmenter: SAM when an endpoint is named in
    settings, the vision fallback otherwise (B-064's decision,
    made in exactly one place)."""
    if getattr(settings, "sam_api_base", ""):
        return SamSegmenter(
            base_url=settings.sam_api_base, api_key=getattr(settings, "sam_api_key", ""),
        )
    return VisionBoxSegmenter(client)
