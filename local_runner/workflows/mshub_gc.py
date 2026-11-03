"""
GC-MS Deconvolution workflow (mshub-gc).
Translates mshub_flow.xml + mshub_tool.xml into subprocess calls.

This is the simplest of the three workflows — entirely Python, no binaries.
Original tool used a dedicated conda env (miniconda3_gamma/envs/mshub-gc).
We run with the gnps conda env since all deps were already updated.

Pipeline (from flow.xml):
  1. preprocess_gcms_data  -> process_gc.py
       inputs:  spectra folder (mzML/CDF files), workflowParameters.xml
       outputs: preprocessing_scratch/, specs_ms.mgf, clusterinfo.tsv,
                clustersummary.tsv, summary_output/
  2. create_quantification -> create_quantification.py
       inputs:  preprocessing_scratch/, workflowParameters.xml
       outputs: quantification.csv
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

if TYPE_CHECKING:
    from orchestrator import Job

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_BASE = REPO_ROOT / "mshub-gc" / "tools" / "mshub-gc"


def run(job: "Job") -> bool:
    p = job.params
    input_dir = job.input_dir
    out = job.output_dir

    python = sys.executable

    # Locate input spectra (mzML or CDF files)
    spectra_files = list(input_dir.glob("*.mzML")) + list(input_dir.glob("*.mzml")) + \
                    list(input_dir.glob("*.CDF")) + list(input_dir.glob("*.cdf")) + \
                    list(input_dir.glob("*.netCDF"))
    if not spectra_files:
        job.log("ERROR: No GC-MS spectra files found (expected .mzML or .CDF)")
        return False

    job.log(f"Found {len(spectra_files)} spectra file(s): {[f.name for f in spectra_files]}")

    # Output paths (match fixed names from tool.xml)
    preprocessing_scratch = out / "preprocessing_scratch"
    clustered_mgf = out / "specs_ms.mgf"
    clusterinfo = out / "clusterinfo.tsv"
    clustersummary = out / "clustersummary.tsv"
    summary_output = out / "summary_output"
    quantification_output = out / "quantification.csv"
    workflow_params_file = out / "workflowParameters.xml"

    preprocessing_scratch.mkdir(exist_ok=True)
    summary_output.mkdir(exist_ok=True)

    _write_workflow_params(workflow_params_file, p)

    # Script paths (from tool.xml pathVar entries)
    process_gc = WORKFLOW_BASE / "process_gc.py"
    create_quant = WORKFLOW_BASE / "create_quantification.py"
    import_script = WORKFLOW_BASE / "proc" / "io" / "importmsdata.py"
    align_script = WORKFLOW_BASE / "proc" / "preproc" / "intrapalign.py"
    noise_script = WORKFLOW_BASE / "proc" / "preproc" / "noisefilter.py"
    interalign_script = WORKFLOW_BASE / "proc" / "preproc" / "interpalign.py"
    peakdetect_script = WORKFLOW_BASE / "proc" / "preproc" / "peakdetect.py"
    export_script = WORKFLOW_BASE / "proc" / "io" / "export.py"
    report_script = WORKFLOW_BASE / "proc" / "io" / "report.py"
    vistic_script = WORKFLOW_BASE / "proc" / "vis" / "vistic.py"

    # ── Step 1: Preprocess GC-MS data ─────────────────────────────────────────
    ok = job.run_step("preprocess_gcms_data", [
        python, str(process_gc),
        str(workflow_params_file),
        str(input_dir),
        str(preprocessing_scratch),
        str(clustered_mgf),
        str(clusterinfo),
        str(clustersummary),
        str(summary_output),
        python,                                          # python.exe arg
        f"import_script={import_script}",
        f"align_script={align_script}",
        f"noise_script={noise_script}",
        f"interalign_script={interalign_script}",
        f"peakdetect_script={peakdetect_script}",
        f"export_script={export_script}",
        f"report_script={report_script}",
        f"vistic_script={vistic_script}",
    ])
    if not ok:
        return False

    # ── Step 2: Create quantification table ───────────────────────────────────
    ok = job.run_step("create_quantification", [
        python, str(create_quant),
        str(preprocessing_scratch),
        str(workflow_params_file),
        str(quantification_output),
    ])
    if not ok:
        return False

    job.log("Key outputs:")
    job.log(f"  Deconvolved MGF:       {clustered_mgf}")
    job.log(f"  Cluster info:          {clusterinfo}")
    job.log(f"  Cluster summary:       {clustersummary}")
    job.log(f"  Quantification table:  {quantification_output}")
    job.log(f"  Summary reports:       {summary_output}")
    return True


def _write_workflow_params(path: Path, params: dict):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<parameters>"]
    for k, v in params.items():
        if v is None or str(v).strip() == "":
            continue
        # Escape XML special characters in both name attribute and text value
        lines.append(f'  <parameter name={quoteattr(k)}>{escape(str(v))}</parameter>')
    lines.append("</parameters>")
    path.write_text("\n".join(lines))