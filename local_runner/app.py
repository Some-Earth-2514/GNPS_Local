"""
GNPS Local - FastAPI application
Replaces the ProteoSAFe web frontend for single-user local use.
"""

import io
import json
import os
import sys
import uuid
import asyncio
import shutil
import zipfile
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

import orchestrator as orc

app = FastAPI(title="GNPS Local", version="1.0.0")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Global task tracker for background index builds
_index_build_tasks: dict[str, dict] = {}  # task_id -> {status, error, start_time}

@app.get("/icon.svg")
async def serve_favicon():
    return FileResponse(str(BASE_DIR / "templates" / "icon.svg"), media_type="image/svg+xml")

@app.get("/static/css/styles.css")
async def serve_css():
    return FileResponse(str(BASE_DIR / "templates" / "styles.css"), media_type="text/css")

@app.get("/static/js/functions.js")
async def serve_js():
    return FileResponse(str(BASE_DIR / "templates" / "functions.js"), media_type="application/javascript")

# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    jobs = orc.list_jobs()
    return templates.TemplateResponse("index.html", {"request": request, "jobs": jobs})


@app.get("/submit/{workflow}", response_class=HTMLResponse)
async def submit_page(request: Request, workflow: str):
    if workflow not in ("molecular_networking", "fbmn", "mshub_gc", "mcn"):
        raise HTTPException(404, "Unknown workflow")
    return templates.TemplateResponse(f"submit_{workflow}.html", {"request": request})


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    job = orc.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return templates.TemplateResponse("job.html", {
        "request": request,
        "job": job.to_dict(),
        "output_files": orc.get_output_files(job_id),
    })


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/jobs")
async def api_list_jobs():
    return orc.list_jobs()


@app.get("/api/job/{job_id}")
async def api_get_job(job_id: str):
    job = orc.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/job/{job_id}/log")
async def api_get_log(job_id: str):
    return {"log": orc.get_log(job_id)}


@app.get("/api/job/{job_id}/files")
async def api_get_files(job_id: str):
    return orc.get_output_files(job_id)


@app.get("/api/job/{job_id}/download/{filename:path}")
async def download_file(job_id: str, filename: str):
    output_dir = orc.JOBS_ROOT / job_id / "output"
    file_path = output_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(file_path), filename=file_path.name)


@app.get("/api/job/{job_id}/download_all")
async def download_all_files(job_id: str):
    """Stream all output files for a job as a single zip archive."""
    job = orc.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    output_dir = orc.JOBS_ROOT / job_id / "output"
    if not output_dir.exists():
        raise HTTPException(404, "No outputs yet")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(output_dir))
    buf.seek(0)

    zip_name = f"gnps_job_{job_id}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_name}"},
    )

@app.post("/api/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = orc.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    
    if job.status not in [orc.JobStatus.RUNNING, orc.JobStatus.QUEUED]:
        return {"status": "error", "message": "Only running or queued jobs can be canceled"}
    
    job.kill_job(reason="Killed by user")
    return {"status": "canceled", "job_id": job_id}

@app.post("/api/job/{job_id}/restart")
async def restart_job(job_id: str):
    job = orc.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # 1. Validation: only allow restart on terminal states
    if job.status not in [orc.JobStatus.DONE, orc.JobStatus.FAILED, orc.JobStatus.CANCELED]:
        raise HTTPException(400, "Only done, failed, or canceled jobs can be restarted.")

    # 2. Cleanup: clear output directory and log file
    #    Input directory is preserved — same files, same params, same job id
    try:
        if job.output_dir.exists():
            shutil.rmtree(job.output_dir)
        job.output_dir.mkdir(exist_ok=True)

        if job.log_file.exists():
            job.log_file.write_text("")

    except Exception as e:
        raise HTTPException(500, f"Failed to clean up job directories: {str(e)}")

    # 3. Reset job state to QUEUED, then start
    job.reset_for_restart()
    orc.start_job(job)

    return {"status": "restarted", "job_id": job_id}

# ── Submit endpoints ───────────────────────────────────────────────────────────

@app.post("/api/submit/molecular_networking")
async def submit_molecular_networking(
    input_spectra: List[UploadFile] = File(...),
    TOLERANCE: float = Form(0.02),
    MIN_MATCHED_PEAKS: int = Form(6),
    SCORE_THRESHOLD: float = Form(0.7),
    MAX_SHIFT: float = Form(500.0),
    TOPK: int = Form(10),
    MAX_COMPONENT_SIZE: int = Form(100),
    FILTER_G6_BLANKS: str = Form("0"),
    MIN_MATCHED_PEAKS_SEARCH: int = Form(6),
    SCORE_THRESHOLD_SEARCH: float = Form(0.7),
    ANALOG_SEARCH: str = Form("0"),
    MAXIMUM_NUMBER_OF_RESULTS: int = Form(1),
    library: Optional[UploadFile] = File(default=None),
    groupmapping: Optional[UploadFile] = File(default=None),
    attributemapping: Optional[UploadFile] = File(default=None),
    metadatafile: Optional[UploadFile] = File(default=None),
):
    params = {
        "TOLERANCE": str(TOLERANCE),
        "MIN_MATCHED_PEAKS": str(MIN_MATCHED_PEAKS),
        "SCORE_THRESHOLD": str(SCORE_THRESHOLD),
        "MAX_SHIFT": str(MAX_SHIFT),
        "TOPK": str(TOPK),
        "MAX_COMPONENT_SIZE": str(MAX_COMPONENT_SIZE),
        "FILTER_G6_BLANKS": FILTER_G6_BLANKS,
        "MIN_MATCHED_PEAKS_SEARCH": str(MIN_MATCHED_PEAKS_SEARCH),
        "SCORE_THRESHOLD_SEARCH": str(SCORE_THRESHOLD_SEARCH),
        "ANALOG_SEARCH": ANALOG_SEARCH,
        "MAXIMUM_NUMBER_OF_RESULTS": str(MAXIMUM_NUMBER_OF_RESULTS),
    }
    job = orc.create_job("molecular_networking", params)
    await _save_uploads(job, input_spectra, subfolder=None)
    if library and library.filename:
        await _save_single_upload(job, library, library.filename)
    if groupmapping and groupmapping.filename:
        await _save_single_upload(job, groupmapping, "groupmapping.csv")
    if attributemapping and attributemapping.filename:
        await _save_single_upload(job, attributemapping, "attributemapping.csv")
    if metadatafile and metadatafile.filename:
        await _save_single_upload(job, metadatafile, "metadata.tsv")
    orc.start_job(job)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/submit/fbmn")
async def submit_fbmn(
    input_spectra: List[UploadFile] = File(...),
    quantification_table: UploadFile = File(...),
    QUANT_TABLE_SOURCE: str = Form("mzmine2"),
    TOLERANCE_ION: float = Form(0.02),
    TOLERANCE_PM: float = Form(0.02),
    MIN_MATCHED_PEAKS: int = Form(6),
    SCORE_THRESHOLD: float = Form(0.7),
    PAIRS_MIN_COSINE: float = Form(0.1),
    MAX_SHIFT: float = Form(500.0),
    TOPK: int = Form(10),
    MAX_COMPONENT_SIZE: int = Form(100),
    FILTER_PRECURSOR_WINDOW: str = Form("1"),
    WINDOW_FILTER: str = Form("1"),
    QUANT_FILE_NORM: str = Form("None"),
    MIN_MATCHED_PEAKS_SEARCH: int = Form(6),
    SCORE_THRESHOLD_SEARCH: float = Form(0.7),
    ANALOG_SEARCH: str = Form("0"),
    RUN_STATS: str = Form("No"),
    METADATA_COLUMN: str = Form(""),
    METADATA_CONDITION_ONE: str = Form(""),
    METADATA_CONDITION_TWO: str = Form(""),
    JOB_NAME: str = Form(""),
    MOLECULAR_COMMUNITY_NETWORKING: str = Form("0"),
    MCN_K: float = Form(20.0),
    MCN_C: float = Form(0.75),
    library: Optional[UploadFile] = File(default=None),
    metadata_table: Optional[UploadFile] = File(default=None),
):
    params = {
        "JOB_NAME": JOB_NAME,
        "QUANT_TABLE_SOURCE": QUANT_TABLE_SOURCE,
        "TOLERANCE_ION": str(TOLERANCE_ION),
        "TOLERANCE_PM": str(TOLERANCE_PM),
        "MIN_MATCHED_PEAKS": str(MIN_MATCHED_PEAKS),
        "SCORE_THRESHOLD": str(SCORE_THRESHOLD),
        "PAIRS_MIN_COSINE": str(PAIRS_MIN_COSINE),
        "MAX_SHIFT": str(MAX_SHIFT),
        "TOPK": str(TOPK),
        "MAX_COMPONENT_SIZE": str(MAX_COMPONENT_SIZE),
        "FILTER_PRECURSOR_WINDOW": FILTER_PRECURSOR_WINDOW,
        "WINDOW_FILTER": WINDOW_FILTER,
        "QUANT_FILE_NORM": QUANT_FILE_NORM,
        "MIN_MATCHED_PEAKS_SEARCH": str(MIN_MATCHED_PEAKS_SEARCH),
        "SCORE_THRESHOLD_SEARCH": str(SCORE_THRESHOLD_SEARCH),
        "ANALOG_SEARCH": ANALOG_SEARCH,
        "RUN_STATS": RUN_STATS,
        "METADATA_COLUMN": METADATA_COLUMN,
        "METADATA_CONDITION_ONE": METADATA_CONDITION_ONE,
        "METADATA_CONDITION_TWO": METADATA_CONDITION_TWO,
        "MOLECULAR_COMMUNITY_NETWORKING": MOLECULAR_COMMUNITY_NETWORKING,
        "MCN_K": str(MCN_K),
        "MCN_C": str(MCN_C),
    }
    job = orc.create_job("fbmn", params)
    await _save_uploads(job, input_spectra, subfolder=None)
    if quantification_table and quantification_table.filename:
        await _save_single_upload(job, quantification_table, quantification_table.filename)
    if library and library.filename:
        await _save_single_upload(job, library, library.filename)
    if metadata_table and metadata_table.filename:
        await _save_single_upload(job, metadata_table, "metadata.tsv")
    orc.start_job(job)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/submit/mshub_gc")
async def submit_mshub_gc(
    input_spectra: List[UploadFile] = File(...),
    FILTER_WINDOW: float = Form(0.5),
    MAX_SHIFT_SECONDS: float = Form(5.0),
    NUM_PEAKS: int = Form(5),
    NOISE_THRESHOLD: float = Form(0.0),
    COSINE_THRESHOLD: float = Form(0.8),
    CLUSTER_MIN_SIZE: int = Form(1),
):
    params = {
        "FILTER_WINDOW": str(FILTER_WINDOW),
        "MAX_SHIFT_SECONDS": str(MAX_SHIFT_SECONDS),
        "NUM_PEAKS": str(NUM_PEAKS),
        "NOISE_THRESHOLD": str(NOISE_THRESHOLD),
        "COSINE_THRESHOLD": str(COSINE_THRESHOLD),
        "CLUSTER_MIN_SIZE": str(CLUSTER_MIN_SIZE),
    }
    job = orc.create_job("mshub_gc", params)
    await _save_uploads(job, input_spectra, subfolder=None)
    orc.start_job(job)
    return {"job_id": job.id, "status": job.status}

@app.post("/api/submit/mcn")
async def submit_mcn(
    input_spectra: List[UploadFile] = File(...),
    MCN_K: float = Form(20.0),
    MCN_C: float = Form(0.75),
):
    params = {
        "MCN_K": str(MCN_K),
        "MCN_C": str(MCN_C),
    }
    job = orc.create_job("mcn", params)
    await _save_uploads(job, input_spectra, subfolder=None)
    orc.start_job(job)
    return {"job_id": job.id, "status": job.status}

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _save_uploads(job, files: List[UploadFile], subfolder: Optional[str]):
    dest = job.input_dir / subfolder if subfolder else job.input_dir
    dest.mkdir(exist_ok=True)
    for f in files:
        if f.filename:
            file_path = dest / Path(f.filename).name
            with open(file_path, "wb") as out:
                shutil.copyfileobj(f.file, out)


async def _save_single_upload(job, file: UploadFile, name: str):
    if file and file.filename:
        file_path = job.input_dir / name
        with open(file_path, "wb") as out:
            shutil.copyfileobj(file.file, out)

# ── Libraries page ─────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "libraries": orc.list_libraries(),
        "storage": orc.get_storage_info(),
    })


# ── Library API ────────────────────────────────────────────────────────────────

@app.get("/api/libraries")
async def api_list_libraries():
    return orc.list_libraries()


@app.post("/api/libraries/upload")
async def api_upload_libraries(files: List[UploadFile] = File(...)):
    """Handle library uploads and trigger background index builds."""
    library_dir = Path(os.environ.get("GNPS_LIBRARIES_DIR", orc.LIBRARIES_ROOT))
    library_dir.mkdir(exist_ok=True, parents=True)
    
    saved = []
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        if not safe_name.lower().endswith(".mgf"):
            continue
        dest = library_dir / safe_name
        
        # Read streaming file bytes securely
        dest.write_bytes(await f.read())
        saved.append(safe_name)
        
    if not saved:
        return {"saved": [], "message": "No valid .mgf library files uploaded."}

    # Trigger background index build
    task_id = str(uuid.uuid4())
    _index_build_tasks[task_id] = {
        "status": "queued",
        "files": saved,
        "start_time": datetime.now().isoformat(),
    }
    
    thread = threading.Thread(
        target=_build_indexes_background,
        args=(library_dir, task_id),
        daemon=True,
    )
    thread.start()
    
    return {
        "saved": saved,
        "task_id": task_id,
        "message": f"Indexing {len(saved)} library file(s) in background..."
    }


@app.get("/api/libraries/index-status/{task_id}")
async def get_index_status(task_id: str):
    """Poll status of background index build."""
    task = _index_build_tasks.get(task_id)
    if not task:
        return {"status": "not_found"}
    return task


@app.delete("/api/libraries/{filename}")
async def api_delete_library(filename: str):
    ok = orc.delete_library(filename)
    if not ok:
        raise HTTPException(404, "Library not found")
    return {"deleted": filename}


# ── Storage info ───────────────────────────────────────────────────────────────

@app.get("/api/storage")
async def api_storage():
    return orc.get_storage_info()


# ── Timings API (parsed from run.log) ─────────────────────────────────────────

def _parse_log_timings(log_text: str) -> list[dict]:
    """
    Parse step timings from run.log.
    Log lines look like:
      [HH:MM:SS] --- STEP: stepname ---
      [HH:MM:SS] STEP OK
      [HH:MM:SS] STEP FAILED (exit N)
      [HH:MM:SS] STEP TIMED OUT ...
      [HH:MM:SS] STEP TERMINATED BY USER
    Duration is computed from the HH:MM:SS timestamps.
    Handles midnight wraparound.
    """
    import re

    step_re  = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\] --- STEP: (.+?) ---')
    end_re   = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\] STEP (OK|FAILED|TIMED OUT|TERMINATED)')

    def _to_secs(hms: str) -> int:
        h, m, s = map(int, hms.split(':'))
        return h * 3600 + m * 60 + s

    pending: dict | None = None
    results = []

    for line in log_text.splitlines():
        m = step_re.match(line)
        if m:
            pending = {"step": m.group(2), "start_s": _to_secs(m.group(1)), "start_hms": m.group(1)}
            continue
        if pending:
            m2 = end_re.match(line)
            if m2:
                end_s  = _to_secs(m2.group(1))
                start_s = pending["start_s"]
                # Midnight wraparound: if end < start, add 86400
                dur = end_s - start_s
                if dur < 0:
                    dur += 86400
                status_word = m2.group(2)
                status = "ok" if status_word == "OK" else (
                    "timeout" if "TIMED" in status_word else (
                    "canceled" if "TERMINATED" in status_word else "failed"))
                results.append({
                    "step":       pending["step"],
                    "duration_s": dur,
                    "status":     status,
                })
                pending = None

    return results


@app.get("/api/job/{job_id}/timings")
async def api_job_timings(job_id: str):
    """Parse and return per-step timings from run.log for one job."""
    log_file = orc.JOBS_ROOT / job_id / "run.log"
    if not log_file.exists():
        return []
    try:
        return _parse_log_timings(log_file.read_text(errors="replace"))
    except Exception:
        return []


@app.get("/api/timings/aggregate")
async def api_timings_aggregate(workflow: str = "all"):
    """Aggregate step timings across all completed jobs, optionally filtered by workflow."""
    from collections import defaultdict

    step_data: dict[str, list[float]] = defaultdict(list)
    job_totals: list[float] = []
    failure_counts: dict[str, int] = defaultdict(int)
    job_count = 0

    for state_file in orc.JOBS_ROOT.glob("*/state.json"):
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            continue
        if state.get("status") not in ("done", "failed"):
            continue
        if workflow != "all" and state.get("workflow") != workflow:
            continue
        log_file = state_file.parent / "run.log"
        if not log_file.exists():
            continue
        try:
            timings = _parse_log_timings(log_file.read_text(errors="replace"))
        except Exception:
            continue
        if not timings:
            continue

        job_count += 1
        job_totals.append(sum(t["duration_s"] for t in timings))
        for t in timings:
            step_data[t["step"]].append(t["duration_s"])
            if t["status"] != "ok":
                failure_counts[t["step"]] += 1

    if not step_data:
        return {"job_count": 0, "steps": [], "avg_total_s": 0, "most_failed_step": None}

    steps = [
        {
            "step":          step,
            "avg_s":         round(sum(d) / len(d), 2),
            "max_s":         round(max(d), 2),
            "min_s":         round(min(d), 2),
            "count":         len(d),
            "failure_count": failure_counts.get(step, 0),
        }
        for step, d in step_data.items()
    ]
    steps.sort(key=lambda s: s["avg_s"], reverse=True)

    return {
        "job_count":       job_count,
        "steps":           steps,
        "avg_total_s":     round(sum(job_totals) / len(job_totals), 2) if job_totals else 0,
        "most_failed_step": max(failure_counts, key=failure_counts.get) if failure_counts else None,
    }


def _build_indexes_background(library_dir: Path, task_id: str):
    """Run in background thread."""
    try:
        _index_build_tasks[task_id]["status"] = "building"
        subprocess.run([
            sys.executable,
            str(Path(__file__).parent / "workflows" / "getGNPS_library_annotations_local.py"),
            "--build_indexes",
            "--library_dir", str(library_dir),
        ], check=True, capture_output=True, timeout=3600)
        _index_build_tasks[task_id]["status"] = "done"
    except Exception as e:
        _index_build_tasks[task_id]["status"] = "error"
        _index_build_tasks[task_id]["error"] = str(e)