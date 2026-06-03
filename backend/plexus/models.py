"""Pydantic schemas — the App Definition contract (see docs/DESIGN.md §4)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Position(BaseModel):
    x: float = 0
    y: float = 0


class Node(BaseModel):
    id: str
    type: str
    label: str = ""
    position: Position = Field(default_factory=Position)
    config: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    id: str
    source: str
    target: str
    # Optional routing label. A flow.branch node marks which labelled edges are
    # "live" (e.g. "true"/"false"); the executor prunes the untaken paths.
    label: str = ""


class AppSettings(BaseModel):
    defaultModelId: Optional[str] = None
    region: Optional[str] = None
    streaming: bool = True


class AppDef(BaseModel):
    schemaVersion: str = "1.0"
    id: Optional[str] = None
    name: str = "Untitled"
    description: str = ""
    canvas: str = "graph"
    settings: AppSettings = Field(default_factory=AppSettings)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class RunRequest(BaseModel):
    app: AppDef
    inputs: dict[str, Any] = Field(default_factory=dict)
