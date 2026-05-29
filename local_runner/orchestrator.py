"""
Orchestrator - replaces ProteoSAFe job engine for single-user local use.
Manages job lifecycle: queued -> running -> done/failed
All subprocess calls run in WSL2/Linux context (repo is on /mnt/d/).
"""

import os
import uuid
import json
import subprocess
import threading
import traceback
import psutil
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

JOBS_ROOT = Path(os.environ.get("GNPS_JOBS_DIR", Path.home() / "gnps_jobs"))
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

LIBRARIES_ROOT = Path(os.environ.get(
    "GNPS_LIBRARIES_DIR",
    REPO_ROOT / "libraries"
))
LIBRARIES_ROOT.mkdir(parents=True, exist_ok=True)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class Job:
    def __init__(self, workflow: str, params: dict):
        self.id = str(uuid.uuid4())[:8]
        self.workflow = workflow
        self.params = params
        self.status = JobStatus.QUEUED
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.error: Optional[str] = None

        # Directories
        self.job_dir = JOBS_ROOT / self.id
        self.input_dir = self.job_dir / "input"
        self.output_dir = self.job_dir / "output"
        self.log_file = self.job_dir / "run.log"

        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)

        self.process: Optional[subprocess.Popen] = None

        self._save_state()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow": self.workflow,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }

    def _save_state(self):
        with open(self.job_dir / "state.json", "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with open(self.log_file, "a") as f:
            f.write(line)

    def run_step(self, step_name: str, cmd: list[str], cwd: Optional[Path] = None, timeout: int = 3600) -> bool:
        """Run a single pipeline step. Returns True on success."""
        self.log(f"--- STEP: {step_name} ---")
        self.log(f"CMD: {' '.join(str(c) for c in cmd)}")
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(cwd or REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )

            try:
                stdout, stderr = self.process.communicate(timeout=timeout)

                if stdout:
                    self.log(stdout)
                if stderr:
                    self.log(f"STDERR: {stderr}")
                if self.process.returncode != 0:
                    if self.status == JobStatus.CANCELED:
                        self.log("STEP TERMINATED BY USER")
                        return False
                    self.log(f"STEP FAILED (exit {self.process.returncode})")
                    return False
                self.log(f"STEP OK")
                return True
            except subprocess.TimeoutExpired:
                self.log(f"STEP TIMED OUT after {timeout} seconds. Killing process group...")
                self.kill_job(reason="Timeout")
                return False
        except Exception as e:
            self.log(f"STEP EXECUTION ERROR: {e}")
            return False
        finally:
            self.process = None

    def kill_job(self, reason: str = "Killed by user"):
        """Force-kill the current process and all its children."""
        if self.process and self.process.poll() is None:
            try:
                # Use psutil to kill the whole process tree (important for WSL/Shell scripts)
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
                self.log(f"Process {self.process.pid} and children terminated.")
            except psutil.NoSuchProcess:
                pass
            
        self.status = JobStatus.CANCELED if "user" in reason.lower() else JobStatus.FAILED
        self.error = reason
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def mark_running(self):
        self.status = JobStatus.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def mark_done(self):
        if self.status != JobStatus.CANCELED:
            self.status = JobStatus.DONE
            self.finished_at = datetime.now(timezone.utc).isoformat()
            self._save_state()

    def mark_failed(self, error: str):
        if self.status != JobStatus.CANCELED:
            self.status = JobStatus.FAILED
            self.finished_at = datetime.now(timezone.utc).isoformat()
            self.error = error
            self._save_state()

    def reset_for_restart(self):
        """Reset job state to QUEUED so it can be re-run with the same inputs and params."""
        self.status = JobStatus.QUEUED
        self.started_at = None
        self.finished_at = None
        self.error = None
        self.process = None
        self._save_state()


# In-memory job registry (single-user, no DB needed)
_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create_job(workflow: str, params: dict) -> Job:
    """Create and register a job WITHOUT starting it.
    Call start_job() only after all input files have been saved to disk."""
    job = Job(workflow, params)
    with _lock:
        _jobs[job.id] = job
    return job


def start_job(job: Job):
    """Start a previously created job in a background thread."""
    thread = threading.Thread(target=_run_job, args=(job,), daemon=True)
    thread.start()


def submit_job(workflow: str, params: dict) -> Job:
    """Create, register, and immediately start a job.
    Only safe when there are no files to upload first (legacy callers)."""
    job = create_job(workflow, params)
    start_job(job)
    return job


def get_job(job_id: str) -> Optional[Job]:
    # Try memory first, then disk
    with _lock:
        if job_id in _jobs:
            return _jobs[job_id]
    # Reconstruct from disk if server restarted
    state_file = JOBS_ROOT / job_id / "state.json"
    if state_file.exists():
        with open(state_file) as f:
            data = json.load(f)
        job = Job.__new__(Job)
        job.__dict__.update(data)
        job.job_dir = JOBS_ROOT / job_id
        job.input_dir = job.job_dir / "input"
        job.output_dir = job.job_dir / "output"
        job.log_file = job.job_dir / "run.log"
        with _lock:
            _jobs[job_id] = job
        return job
    return None


def list_jobs() -> list[dict]:
    """List all jobs from disk (survives restarts)."""
    jobs = []
    for state_file in sorted(JOBS_ROOT.glob("*/state.json"), reverse=True):
        try:
            with open(state_file) as f:
                jobs.append(json.load(f))
        except Exception:
            pass
    return jobs


def get_log(job_id: str) -> str:
    log_file = JOBS_ROOT / job_id / "run.log"
    if log_file.exists():
        return log_file.read_text()
    return ""


def get_output_files(job_id: str) -> list[dict]:
    output_dir = JOBS_ROOT / job_id / "output"
    if not output_dir.exists():
        return []
    files = []
    for f in sorted(output_dir.rglob("*")):
        if f.is_file():
            files.append({
                "name": f.name,
                "path": str(f.relative_to(output_dir)),
                "size": f.stat().st_size,
            })
    return files


def _run_job(job: Job):
    """Dispatch job to the correct workflow runner."""
    from workflows import molecular_networking, fbmn, mshub_gc, mcn

    job.mark_running()
    job.log(f"Starting workflow: {job.workflow}")

    try:
        runners = {
            "molecular_networking": molecular_networking.run,
            "fbmn": fbmn.run,
            "mshub_gc": mshub_gc.run,
            "mcn": mcn.run,
        }
        runner = runners.get(job.workflow)
        if not runner:
            raise ValueError(f"Unknown workflow: {job.workflow}")

        success = runner(job)
        if success:
            job.mark_done()
            job.log("Workflow completed successfully.")
        else:
            job.mark_failed("One or more steps failed. Check log for details.")
    except Exception as e:
        job.mark_failed(str(e))
        job.log(f"FATAL: {traceback.format_exc()}")

def get_storage_info() -> dict:
    """Return job storage path and disk usage for the UI."""
    def _dir_size(p):
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    total_bytes = _dir_size(JOBS_ROOT) if JOBS_ROOT.exists() else 0
    job_count = len([d for d in JOBS_ROOT.iterdir() if d.is_dir()]) if JOBS_ROOT.exists() else 0

    return {
        "jobs_dir": str(JOBS_ROOT),
        "libraries_dir": str(LIBRARIES_ROOT),
        "job_count": job_count,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 1),
    }

def list_libraries() -> list:
    """Return all .mgf files in LIBRARIES_ROOT."""
    if not LIBRARIES_ROOT.exists():
        return []
    libs = []
    for f in sorted(LIBRARIES_ROOT.glob("*.mgf")):
        libs.append({
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
        })
    return libs

def delete_library(filename: str) -> bool:
    """Delete a library MGF by filename. Returns True if deleted."""
    safe_name = Path(filename).name
    target = LIBRARIES_ROOT / safe_name
    if target.exists() and target.suffix.lower() == ".mgf":
        target.unlink()
        return True
    return False