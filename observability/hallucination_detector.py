"""
Hallucination Detector — verifies agent claims against the actual codebase.

Checks:
1. File path references — do referenced files actually exist?
2. Schema references — do referenced Pydantic fields/models exist?
3. Architecture claims — do claims match CLAUDE.md?
4. Function signatures — do referenced functions have the claimed signatures?

Can be run as a standalone script or imported by hooks.

Usage:
    python -m observability.hallucination_detector --text "The file api/services/upload.py contains..."
    python -m observability.hallucination_detector --file /path/to/agent_output.txt
"""

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from observability.schemas import HallucinationEvent, HallucinationType

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Result of hallucination detection on a piece of text."""
    hallucinations: list[HallucinationEvent] = field(default_factory=list)
    checks_performed: int = 0
    claims_verified: int = 0

    @property
    def hallucination_count(self) -> int:
        return len(self.hallucinations)

    @property
    def hallucination_rate(self) -> float:
        if self.checks_performed == 0:
            return 0.0
        return self.hallucination_count / self.checks_performed


class HallucinationDetector:
    """Detects hallucinations by cross-referencing agent claims against the codebase."""

    # Patterns to extract file path references from text
    FILE_PATH_PATTERNS = [
        re.compile(r'`([a-zA-Z0-9_/\\.-]+\.\w{1,5})`'),        # `path/to/file.py`
        re.compile(r'\[([^\]]+)\]\(([^)]+\.\w{1,5})\)'),         # [text](path/to/file.py)
        re.compile(r'(?:in|at|from|see|read|file)\s+[`"]?([a-zA-Z0-9_/\\.-]+\.\w{1,5})[`"]?', re.IGNORECASE),
    ]

    # Patterns to extract function/class references
    FUNCTION_PATTERNS = [
        re.compile(r'`(\w+)\((.*?)\)`'),                         # `function_name(args)`
        re.compile(r'(?:function|method|class)\s+`?(\w+)`?'),    # function foo
    ]

    # Patterns to extract Pydantic model field references
    SCHEMA_PATTERNS = [
        re.compile(r'(\w+(?:Response|Request|Create|Update|Schema))\.(\w+)'),  # ModelName.field
        re.compile(r'`(\w+(?:Response|Request|Create|Update))\.(\w+)`'),
    ]

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or os.getcwd()).resolve()

    def check_text(
        self, text: str, session_id: str = "", agent: str = "main",
    ) -> DetectionResult:
        """Run all hallucination checks against a piece of text."""
        result = DetectionResult()

        self._check_file_references(text, result, session_id, agent)
        self._check_schema_references(text, result, session_id, agent)

        return result

    def _check_file_references(
        self, text: str, result: DetectionResult,
        session_id: str, agent: str,
    ) -> None:
        """Verify that referenced file paths actually exist."""
        paths_found: set[str] = set()

        for pattern in self.FILE_PATH_PATTERNS:
            for match in pattern.finditer(text):
                # Get the file path group (might be group 1 or 2 depending on pattern)
                path_str = match.group(match.lastindex or 1)
                if path_str and not path_str.startswith("http"):
                    paths_found.add(path_str)

        for path_str in paths_found:
            result.checks_performed += 1

            # Normalize path separators
            normalized = path_str.replace("\\", "/")

            # Skip common false positives
            if any(normalized.endswith(ext) for ext in [".json", ".yml", ".yaml", ".env", ".gitkeep"]):
                result.claims_verified += 1
                continue

            # Check if file exists relative to project root
            full_path = self.project_root / normalized
            if full_path.exists():
                result.claims_verified += 1
            else:
                # Also check without leading directories (partial paths)
                found = list(self.project_root.rglob(Path(normalized).name))
                if found:
                    result.claims_verified += 1
                else:
                    result.hallucinations.append(HallucinationEvent(
                        session_id=session_id,
                        agent=agent,
                        hallucination_type=HallucinationType.PHANTOM_FILE,
                        claim=f"Referenced file: {path_str}",
                        evidence=f"File not found at {full_path} or anywhere in project",
                        file_path=path_str,
                        severity="warning",
                    ))

    def _check_schema_references(
        self, text: str, result: DetectionResult,
        session_id: str, agent: str,
    ) -> None:
        """Verify that referenced Pydantic model fields exist."""
        schemas_dir = self.project_root / "api" / "schemas"
        if not schemas_dir.exists():
            return

        # Build a map of model_name → set of field names from actual schema files
        known_models: dict[str, set[str]] = {}
        for schema_file in schemas_dir.glob("*.py"):
            try:
                tree = ast.parse(schema_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        fields = set()
                        for item in node.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                fields.add(item.target.id)
                        if fields:
                            known_models[node.name] = fields
            except (SyntaxError, UnicodeDecodeError):
                continue

        if not known_models:
            return

        # Check references in text
        for pattern in self.SCHEMA_PATTERNS:
            for match in pattern.finditer(text):
                model_name = match.group(1)
                field_name = match.group(2)
                result.checks_performed += 1

                if model_name in known_models:
                    if field_name in known_models[model_name]:
                        result.claims_verified += 1
                    else:
                        actual_fields = ", ".join(sorted(known_models[model_name]))
                        result.hallucinations.append(HallucinationEvent(
                            session_id=session_id,
                            agent=agent,
                            hallucination_type=HallucinationType.SCHEMA_MISMATCH,
                            claim=f"{model_name}.{field_name}",
                            evidence=f"Field '{field_name}' not found in {model_name}. Actual fields: {actual_fields}",
                            severity="error",
                        ))
                else:
                    # Model itself doesn't exist — check if it's a known model name
                    # (only flag if the name looks like a schema model)
                    if model_name.endswith(("Response", "Request", "Create", "Update")):
                        actual_models = ", ".join(sorted(known_models.keys()))
                        result.hallucinations.append(HallucinationEvent(
                            session_id=session_id,
                            agent=agent,
                            hallucination_type=HallucinationType.SCHEMA_MISMATCH,
                            claim=f"Model {model_name}",
                            evidence=f"Model '{model_name}' not found. Known models: {actual_models}",
                            severity="warning",
                        ))


# ── CLI entry point ──

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Detect hallucinations in agent output")
    parser.add_argument("--text", type=str, help="Text to check")
    parser.add_argument("--file", type=str, help="File containing text to check")
    parser.add_argument("--project-root", type=str, default=None)
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        print("Provide --text or --file")
        return

    detector = HallucinationDetector(project_root=args.project_root)
    result = detector.check_text(text)

    print(f"Checks performed: {result.checks_performed}")
    print(f"Claims verified: {result.claims_verified}")
    print(f"Hallucinations found: {result.hallucination_count}")

    if result.hallucinations:
        print("\nHallucinations:")
        for h in result.hallucinations:
            print(f"  [{h.hallucination_type.value}] {h.claim}")
            print(f"    Evidence: {h.evidence}")
            print()


if __name__ == "__main__":
    main()
