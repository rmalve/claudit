"""
Base adapter interface for project-specific observability configuration.

Each project implements an Adapter subclass that maps its domain
to the framework's telemetry schemas.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class ProjectAdapter(ABC):
    """Base class for project-specific observability adapters."""

    @property
    @abstractmethod
    def project_name(self) -> str:
        """Unique project identifier used in all telemetry."""
        ...

    @property
    @abstractmethod
    def project_root(self) -> Path:
        """Root directory of the project (for hallucination detection)."""
        ...

    @property
    def agent_definitions_dir(self) -> Path:
        """Directory containing agent .md definitions."""
        return self.project_root / ".claude" / "agents"

    @property
    def claude_md_paths(self) -> list[Path]:
        """All CLAUDE.md files to check against for architecture claims."""
        paths = []
        root_claude = self.project_root / "CLAUDE.md"
        if root_claude.exists():
            paths.append(root_claude)
        for child in self.project_root.iterdir():
            if child.is_dir():
                child_claude = child / "CLAUDE.md"
                if child_claude.exists():
                    paths.append(child_claude)
        return paths

    @property
    def schema_dir(self) -> Path | None:
        """Directory containing Pydantic schema files (for schema verification)."""
        schemas = self.project_root / "api" / "schemas"
        return schemas if schemas.exists() else None

    def get_versionable_files(self) -> list[Path]:
        """Files that are versioned (agents, CLAUDE.md, skills)."""
        files = []
        if self.agent_definitions_dir.exists():
            files.extend(self.agent_definitions_dir.glob("*.md"))
        files.extend(self.claude_md_paths)
        return files
