"""
Audit Platform Orchestrator

Process supervisor that spawns and manages the Audit Director and all
auditor agents as isolated subprocesses. Each agent runs as its own
Python process with its own ClaudeSDKClient instance.

Reads active projects from config/projects.json and passes them to
all agent processes.

Lifecycle rules:
- Director starts first; auditors start after Director is running
- If Director process dies, auditors are paused until Director restarts
- Auto-restart on agent failure (max 3 attempts, then log and continue)
- Everything stops if Redis is down
- Graceful shutdown on SIGINT/SIGTERM

Usage:
    python orchestrator.py                              # per-session audit (default)
    python orchestrator.py --mode cross-session         # cross-session audit (drift+cost)
    python orchestrator.py --projects foo,bar            # filter to specific projects
    python orchestrator.py --director-only              # start Director only
    python orchestrator.py --auditors-only              # start auditors only
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from observability.stream_client import StreamClient
from observability.messages import MessageEnvelope

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orchestrator")

REPO_ROOT = Path(__file__).parent
AGENTS_DIR = REPO_ROOT / "agents"
PROJECTS_CONFIG = REPO_ROOT / "config" / "projects.json"
MAX_RESTART_ATTEMPTS = 3
HEALTH_CHECK_INTERVAL = 5  # seconds; controls exit-detection latency in parallel phases
PIPELINE_LOG_INTERVAL = 30  # seconds; throttle for pipeline status output


class ProcessState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


@dataclass
class ProjectConfig:
    name: str
    active: bool = True
    root: str = ""
    description: str = ""


@dataclass
class ManagedProcess:
    name: str
    role: str
    cmd: list[str]
    state: ProcessState = ProcessState.STOPPED
    process: subprocess.Popen | None = None
    restart_count: int = 0


def load_projects() -> list[ProjectConfig]:
    """Load active projects from config/projects.json."""
    if not PROJECTS_CONFIG.exists():
        logger.warning("Projects config not found at %s", PROJECTS_CONFIG)
        return []

    try:
        data = json.loads(PROJECTS_CONFIG.read_text(encoding="utf-8"))
        projects = []
        for entry in data.get("projects", []):
            p = ProjectConfig(
                name=entry["name"],
                active=entry.get("active", True),
                root=entry.get("root", ""),
                description=entry.get("description", ""),
            )
            if p.active:
                projects.append(p)
            else:
                logger.info("Project '%s' is inactive, skipping.", p.name)
        return projects
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse projects config: %s", e)
        return []


def check_redis() -> bool:
    """Verify Redis is available."""
    try:
        client = StreamClient.for_director()
        alive = client.ping()
        client.close()
        return alive
    except Exception as e:
        logger.error("Redis health check failed: %s", e)
        return False


class Orchestrator:
    """Process supervisor for audit platform agents.

    Spawns each agent as an isolated subprocess. Monitors health
    and restarts failed processes.
    """

    PER_SESSION_AUDITORS = ["trace", "safety", "policy", "hallucination"]
    CROSS_SESSION_AUDITORS = ["drift", "cost"]
    AUDITOR_TYPES = PER_SESSION_AUDITORS + CROSS_SESSION_AUDITORS

    def __init__(self, mode: str = "per-session", project_names: list[str] | None = None):
        self.mode = mode
        all_projects = load_projects()

        # Optional filter: only keep projects whose name matches the override list
        if project_names is not None:
            allowed = set(project_names)
            self.projects = [p for p in all_projects if p.name in allowed]
        else:
            self.projects = all_projects

        self.processes: dict[str, ManagedProcess] = {}
        self._shutdown = False

        if not self.projects:
            logger.critical(
                "No active projects in %s. Run the onboarding script first.",
                PROJECTS_CONFIG,
            )
            sys.exit(1)

        # Generate a unique audit cycle ID for this run
        self.audit_cycle_id = f"cycle-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        self._project_csv = ",".join(p.name for p in self.projects)
        self._python = sys.executable
        self._register_processes()

    def _register_processes(self) -> None:
        """Register agent processes based on orchestrator mode.

        Per-session mode phases:
          1. Director (per-session-assign) — queries data, publishes tasks
          2. Trace Auditor — builds timelines from raw events
          3. Safety, policy, hallucination auditors (parallel)
          4. Director (synthesize) — reads findings, writes report

        Cross-session mode phases:
          1. Director (cross-session-assign) — queries data, publishes tasks
          3. Drift + cost auditors (parallel)
          4. Director (synthesize) — reads findings, writes report

        Both modes always register director:assign and director:synthesize.
        """
        # Determine assign mode string and auditor list based on orchestrator mode
        if self.mode == "cross-session":
            assign_mode = "cross-session-assign"
            auditor_types = self.CROSS_SESSION_AUDITORS
        else:
            assign_mode = "per-session-assign"
            auditor_types = self.PER_SESSION_AUDITORS

        # Director assigns tasks
        self.processes["director:assign"] = ManagedProcess(
            name="Director (assign)",
            role="director",
            cmd=[
                self._python,
                str(AGENTS_DIR / "run_director.py"),
                "--projects", self._project_csv,
                "--mode", assign_mode,
                "--max-turns", "100",
            ],
        )

        # Register auditors for this mode
        for auditor_type in auditor_types:
            key = f"auditor:{auditor_type}"
            self.processes[key] = ManagedProcess(
                name=f"{auditor_type.title()} Auditor",
                role=key,
                cmd=[
                    self._python,
                    str(AGENTS_DIR / "run_auditor.py"),
                    "--type", auditor_type,
                    "--projects", self._project_csv,
                    "--max-turns", "50",
                ],
            )

        # Director synthesizes findings
        self.processes["director:synthesize"] = ManagedProcess(
            name="Director (synthesize)",
            role="director",
            cmd=[
                self._python,
                str(AGENTS_DIR / "run_director.py"),
                "--projects", self._project_csv,
                "--mode", "synthesize",
                "--max-turns", "200",
            ],
        )

    def _start_process(self, proc: ManagedProcess) -> bool:
        """Start a single agent subprocess."""
        try:
            proc.state = ProcessState.STARTING
            logger.info("Starting %s...", proc.name)

            env = {**os.environ, "AUDIT_CYCLE_ID": self.audit_cycle_id}
            # HARDEN-001: set OBSERVABILITY_PROJECT so stream_publish can
            # auto-inject the project field on any payload. Single-project
            # today; multi-project Chief Director era will need task-payload
            # based project context instead.
            if self.projects:
                env["OBSERVABILITY_PROJECT"] = self.projects[0].name
            # Pass agent identity so hooks and MCP tools resolve the correct agent
            if proc.role == "director":
                env["AGENT_NAME"] = "director"
            elif proc.role.startswith("auditor:"):
                auditor_type = proc.role.split(":")[1]
                env["AUDITOR_TYPE"] = auditor_type
                env["AGENT_NAME"] = f"auditor-{auditor_type}"

            proc.process = subprocess.Popen(
                proc.cmd,
                stdout=subprocess.PIPE,
                stderr=None,  # Let agent logs flow to orchestrator's console
                cwd=str(REPO_ROOT),
                env=env,
            )

            # Give it a moment to fail fast
            time.sleep(2)
            if proc.process.poll() is not None:
                returncode = proc.process.returncode
                stderr = proc.process.stderr.read().decode() if proc.process.stderr else ""
                logger.error(
                    "%s exited immediately (code %d): %s",
                    proc.name, returncode, stderr[:500],
                )
                proc.state = ProcessState.FAILED
                return False

            proc.state = ProcessState.RUNNING
            logger.info("%s started (PID %d).", proc.name, proc.process.pid)
            return True

        except Exception as e:
            logger.error("Failed to start %s: %s", proc.name, e)
            proc.state = ProcessState.FAILED
            return False

    def _stop_process(self, proc: ManagedProcess) -> None:
        """Stop a single agent subprocess."""
        if proc.process and proc.process.poll() is None:
            logger.info("Stopping %s (PID %d)...", proc.name, proc.process.pid)
            proc.process.terminate()
            try:
                proc.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Force killing %s", proc.name)
                proc.process.kill()
        proc.state = ProcessState.STOPPED

    def _restart_process(self, proc: ManagedProcess) -> bool:
        """Restart a failed agent."""
        if proc.restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(
                "%s failed after %d restart attempts. Giving up.",
                proc.name, MAX_RESTART_ATTEMPTS,
            )
            proc.state = ProcessState.FAILED
            return False

        proc.restart_count += 1
        logger.info(
            "Restarting %s (attempt %d/%d)...",
            proc.name, proc.restart_count, MAX_RESTART_ATTEMPTS,
        )

        self._stop_process(proc)
        return self._start_process(proc)

    def _check_health(self) -> None:
        """Check all running processes and log any that exited.

        Agents are NOT auto-restarted to conserve token cost.
        Agents complete their audit cycle and exit cleanly (code 0).
        Use --restart-failed to restart only agents that crashed (non-zero exit).
        """
        for key, proc in self.processes.items():
            if proc.state != ProcessState.RUNNING:
                continue

            if proc.process and proc.process.poll() is not None:
                returncode = proc.process.returncode
                proc.state = ProcessState.STOPPED

                if returncode == 0:
                    logger.info(
                        "%s completed audit cycle (exit 0).", proc.name,
                    )
                else:
                    logger.warning(
                        "%s exited with error (code %d).", proc.name, returncode,
                    )

                if proc.role == "director" and returncode != 0:
                    logger.critical("DIRECTOR IS DOWN (code %d).", returncode)

    def _log_task_pipeline(self) -> None:
        """Print task pipeline status for each auditor to console."""
        try:
            client = StreamClient.for_director()

            # Count assigned tasks per auditor
            assigned: dict[str, int] = {}
            results = client._redis.xrange("audit:tasks", count=1000)
            for _, data in results:
                try:
                    env = MessageEnvelope.from_stream_dict(data)
                    auditor = (
                        env.payload.get("target_auditor")
                        or env.target.removeprefix("auditor:").strip()
                        or "unknown"
                    )
                    assigned[auditor] = assigned.get(auditor, 0) + 1
                except Exception:
                    continue

            # Count completions and failures
            completed: dict[str, int] = {}
            failed: dict[str, int] = {}
            results = client._redis.xrange("audit:status", count=2000)
            for _, data in results:
                try:
                    env = MessageEnvelope.from_stream_dict(data)
                    p = env.payload
                    status_type = p.get("status_type", "")
                    auditor = p.get("auditor", "unknown")
                    if auditor.startswith("auditor:"):
                        auditor = auditor[len("auditor:"):]
                    if status_type == "task_complete":
                        completed[auditor] = completed.get(auditor, 0) + 1
                    elif status_type == "task_failed":
                        failed[auditor] = failed.get(auditor, 0) + 1
                except Exception:
                    continue

            # Count Director activity across streams
            tasks_issued = sum(assigned.values())
            findings_count = client._redis.xlen("audit:findings")
            directives_count = client._redis.xlen("audit:directives")
            escalations_count = client._redis.xlen("audit:escalations")
            reports_count = client._redis.xlen("audit:reports")

            client.close()

            # Build and print pipeline
            all_auditors = sorted(set(assigned) | set(completed) | set(failed))
            if not all_auditors and tasks_issued == 0:
                return

            logger.info("─── Task Pipeline ───────────────────────────")

            # Director progress line
            director_proc = self.processes.get("director:assign") or self.processes.get("director:synthesize")
            director_state = director_proc.state.value if director_proc else "unknown"
            director_steps = []
            if tasks_issued > 0:
                director_steps.append(f"tasks:{tasks_issued}")
            if findings_count > 0:
                director_steps.append(f"findings:{findings_count}")
            if directives_count > 0:
                director_steps.append(f"directives:{directives_count}")
            if escalations_count > 0:
                director_steps.append(f"escalations:{escalations_count}")
            if reports_count > 0:
                director_steps.append(f"reports:{reports_count}")

            # Director phase detection
            if reports_count > 0:
                phase = "report sent ✓"
            elif directives_count > 0:
                phase = "issuing directives"
            elif findings_count > 0:
                phase = "reviewing findings"
            elif tasks_issued > 0:
                phase = "waiting for auditors"
            else:
                phase = "initializing"

            steps_str = ", ".join(director_steps) if director_steps else "starting"
            logger.info("  %-15s [%s] %s", "director", phase, steps_str)

            # Auditor progress lines
            total_assigned = 0
            total_pending = 0
            for auditor in all_auditors:
                a = assigned.get(auditor, 0)
                c = completed.get(auditor, 0)
                f = failed.get(auditor, 0)
                pending = max(0, a - c - f)
                total_assigned += a
                total_pending += pending

                # Progress bar
                done = c + f
                bar_len = 20
                filled = int(bar_len * done / a) if a > 0 else bar_len
                bar = "█" * filled + "░" * (bar_len - filled)

                status = f"{auditor:<15} [{bar}] {done}/{a}"
                if f > 0:
                    status += f" ({f} failed)"
                if pending == 0 and a > 0:
                    status += " ✓"

                logger.info("  %s", status)

            logger.info("  Total: %d assigned, %d pending", total_assigned, total_pending)
            logger.info("─────────────────────────────────────────────")

        except Exception as e:
            logger.debug("Task pipeline check failed: %s", e)

    def _run_phase(self, proc: ManagedProcess, phase_name: str) -> bool:
        """Run a single process and wait for it to complete. Returns True on success."""
        logger.info("═══ %s ═══", phase_name)
        if not self._start_process(proc):
            logger.error("%s failed to start.", phase_name)
            return False
        if proc.process:
            proc.process.wait()
            proc.state = ProcessState.STOPPED
            if proc.process.returncode == 0:
                logger.info("%s completed successfully.", phase_name)
                return True
            else:
                logger.warning("%s exited with code %d.", phase_name, proc.process.returncode)
                return False
        return False

    def _run_parallel_phase(self, procs: list[ManagedProcess], phase_name: str) -> None:
        """Run multiple processes in parallel and wait for all to complete."""
        logger.info("═══ %s ═══", phase_name)
        for proc in procs:
            self._start_process(proc)

        # Monitor until all complete
        last_pipeline_log = 0.0
        while not self._shutdown:
            time.sleep(HEALTH_CHECK_INTERVAL)

            if not check_redis():
                logger.critical("Redis is down during %s.", phase_name)
                break

            self._check_health()
            now = time.time()
            if now - last_pipeline_log >= PIPELINE_LOG_INTERVAL:
                self._log_task_pipeline()
                last_pipeline_log = now

            alive = [p for p in procs if p.state == ProcessState.RUNNING]
            if not alive:
                logger.info("%s: all agents completed.", phase_name)
                break

        for proc in procs:
            proc.state = ProcessState.STOPPED

    def _run_per_session_cycle(self) -> None:
        """Run per-session audit phases 1-5.

        Phase 1: Director assigns tasks (per-session-assign)
        Phase 2: Trace Auditor builds timelines
        Phase 3: Safety, policy, hallucination auditors in parallel
        Phase 4: Director synthesizes report
        Phase 5: Mark audited events in QDrant
        """
        # Pre-check: if no pending events to audit, skip LLM-powered phases.
        # Cycle-boundary checks (Phase 6) still run to advance directive deadlines.
        try:
            from observability.qdrant_backend import QdrantBackend
            pending = QdrantBackend().count_pending_audit()
            logger.info("Pending events to audit: %d", pending)
        except Exception as e:
            logger.warning("Failed to check pending audit count: %s", e)
            pending = None  # Unknown — proceed as normal

        if pending == 0:
            logger.info("═══ No pending events; skipping audit phases 1-5 ═══")
            return

        # Phase 1: Director assigns tasks
        assign_proc = self.processes["director:assign"]
        if not self._run_phase(assign_proc, "Phase 1: Director assigns tasks"):
            logger.critical("Director assignment failed. Cannot proceed.")
            self.shutdown()
            return

        # Phase 2: Trace Auditor builds timelines
        trace_proc = self.processes["auditor:trace"]
        self._run_phase(trace_proc, "Phase 2: Trace Auditor builds timelines")

        # Phase 3: Other per-session auditors in parallel
        other_auditors = [
            proc for key, proc in self.processes.items()
            if key.startswith("auditor:") and key != "auditor:trace"
        ]
        self._run_parallel_phase(other_auditors, "Phase 3: Auditors analyze findings")

        # Phase 4: Director synthesizes
        synth_proc = self.processes["director:synthesize"]
        self._run_phase(synth_proc, "Phase 4: Director synthesizes report")

        # Phase 5: Mark audited events in QDrant
        logger.info("═══ Phase 5: Marking audited events ═══")
        try:
            from observability.qdrant_backend import QdrantBackend
            qb = QdrantBackend()
            # Find sessions with unaudited events
            unaudited = qb.scroll_all("tool_calls", filters={"audited__ne": True}, limit=10000)
            session_ids = set(p.get("payload", {}).get("session_id", "") for p in unaudited)
            session_ids.discard("")

            total_marked = 0
            for sid in session_ids:
                count = qb.mark_session_audited(sid)
                total_marked += count
                logger.info("  Marked %d events as audited for session %s", count, sid[:12])

            logger.info("Total: %d events marked as audited across %d sessions",
                        total_marked, len(session_ids))
        except Exception as e:
            logger.warning("Failed to mark audited events: %s", e)

    def _run_cross_session_cycle(self) -> None:
        """Run cross-session audit phases.

        Phase 1: Director assigns tasks (cross-session-assign)
        Phase 3: Drift + cost auditors in parallel
        Phase 4: Director synthesizes report
        (No Phase 2 trace, no Phase 5 marking)
        """
        # Phase 1: Director assigns tasks
        assign_proc = self.processes["director:assign"]
        if not self._run_phase(assign_proc, "Phase 1: Director assigns tasks"):
            logger.critical("Director assignment failed. Cannot proceed.")
            self.shutdown()
            return

        # Phase 3: Cross-session auditors in parallel (explicit filter)
        cs_keys = {f"auditor:{a}" for a in self.CROSS_SESSION_AUDITORS}
        cs_auditors = [
            proc for key, proc in self.processes.items()
            if key in cs_keys
        ]
        self._run_parallel_phase(cs_auditors, "Phase 3: Cross-session auditors analyze")

        # Phase 4: Director synthesizes
        synth_proc = self.processes["director:synthesize"]
        self._run_phase(synth_proc, "Phase 4: Director synthesizes report")

    def start(self) -> None:
        """Start the audit platform with phased orchestration.

        Dispatches to per-session or cross-session cycle based on self.mode,
        then runs Phase 6 archival unconditionally.
        """
        project_names = [p.name for p in self.projects]

        logger.info("=" * 60)
        logger.info("LLM Observability Audit Platform")
        logger.info("Audit cycle: %s", self.audit_cycle_id)
        logger.info("Mode: %s", self.mode)
        logger.info("Active projects: %s", ", ".join(project_names))
        if self.mode == "per-session":
            logger.info("Orchestration: phased (assign → trace → auditors → synthesize)")
        else:
            logger.info("Orchestration: phased (assign → drift+cost → synthesize)")
        logger.info("=" * 60)

        # Verify Redis
        if not check_redis():
            logger.critical("Redis is not available. Cannot start.")
            sys.exit(1)

        logger.info("Redis: OK")

        try:
            # Dispatch to mode-specific cycle
            if self.mode == "cross-session":
                self._run_cross_session_cycle()
            else:
                self._run_per_session_cycle()

            # Phase 6: Archive streams to SQLite + run cycle-boundary checks
            logger.info("═══ Phase 6: Archiving streams to SQLite ═══")
            try:
                from observability.archiver import StreamArchiver
                from observability.qdrant_backend import QdrantBackend
                archiver = StreamArchiver(qdrant=QdrantBackend())
                try:
                    results = archiver.archive_cycle(audit_cycle_id=self.audit_cycle_id)
                    logger.info("Archive results: %s", results)
                finally:
                    archiver.close()
            except Exception as e:
                logger.warning("Failed to archive streams: %s", e)

            logger.info("=" * 60)
            logger.info("Audit cycle complete: %s", self.audit_cycle_id)
            logger.info("=" * 60)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")

        self.shutdown()

    def shutdown(self) -> None:
        """Stop all agent processes."""
        self._shutdown = True
        logger.info("Shutting down audit platform...")

        for key, proc in self.processes.items():
            self._stop_process(proc)

        logger.info("Audit platform shut down.")

    def status(self) -> dict:
        """Get current status of all processes."""
        return {
            "projects": [p.name for p in self.projects],
            "agents": {
                key: {
                    "name": proc.name,
                    "role": proc.role,
                    "state": proc.state.value,
                    "pid": proc.process.pid if proc.process and proc.process.poll() is None else None,
                    "restart_count": proc.restart_count,
                }
                for key, proc in self.processes.items()
            },
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM Observability Audit Platform Orchestrator"
    )
    parser.add_argument(
        "--mode", choices=["per-session", "cross-session"],
        default="per-session",
        help="Audit mode: per-session (trace/safety/policy/hallucination) "
             "or cross-session (drift/cost). Default: per-session",
    )
    parser.add_argument(
        "--projects", type=str, default=None,
        help="Comma-separated project names to audit (overrides projects.json filter)",
    )
    args = parser.parse_args()

    project_names = [p.strip() for p in args.projects.split(",")] if args.projects else None
    orchestrator = Orchestrator(mode=args.mode, project_names=project_names)

    def handle_signal(sig, frame):
        logger.info("Received signal %s.", sig)
        orchestrator.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    orchestrator.start()


if __name__ == "__main__":
    main()
