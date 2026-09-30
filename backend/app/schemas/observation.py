from __future__ import annotations

from pydantic import BaseModel, Field


class Box(BaseModel):
    x: float
    y: float
    width: float
    height: float


class Element(BaseModel):
    """An element the agent can act on, as seen in one observation.

    `ref` is only valid for the observation it came from. Before acting, the
    browser layer re-resolves it (see app.browser.elements.resolve).
    """

    ref: str
    role: str
    name: str = ""
    value: str | None = None
    url: str | None = None
    states: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    frame: str = "main"
    in_viewport: bool = True
    box: Box | None = None

    def describe(self) -> str:
        return f'{self.role} "{self.name}"' if self.name else self.role


class Viewport(BaseModel):
    width: int
    height: int
    scroll_y: int = 0
    page_height: int = 0


class Observation(BaseModel):
    sequence: int
    url: str
    title: str
    viewport: Viewport
    elements: list[Element]
    text: str = Field(description="Compact page outline shown to the model")
    omitted_lines: int = 0
    notices: list[str] = Field(default_factory=list)
    fingerprint: str = Field(description="Hash of the outline, for loop detection")
    screenshot_path: str | None = None
