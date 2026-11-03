"""
Molecular Networking workflow (metabolomics-snets-v2).
Translates flow.xml + tool.xml into sequential Python subprocess calls.

Core pipeline (from flow.xml):
  1. specnetsparamsgen       - generate clustering params
  2. msclustering            - MSCluster spectral clustering  
  3. metabolomicclusterinfo  - build clusterinfo + summary
  4. metabolomicmetadatamerge / groupmappingconvert - metadata
  5. copy_filter_spectra_mgf - filter to specs_ms.mgf
  6. prep_molecular_networking_parameters - split into parallel chunks
  7. molecular_networking_parallel_step (x N) - compute pairs
  8. merge_networking_tsv_files - merge pairs TSVs
  9. metabolomic_network_edges_display - filter edges
 10. metabolomics_convert_graphml - produce .graphml for Cytoscape
 11. library search (steps: param gen -> parallel search -> merge -> DB annot)

Skipped (require external services):
  - find_dataset_matches (MassIVE — not available locally)
  - run_qiime2 (optional stats, separate env)
  - create_ili_output (optional 3D mapping)
  - create_linkout_file (MassIVE submission)
"""

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

if TYPE_CHECKING:
    from orchestrator import Job

# Path to the workflow tools directory
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_BASE = REPO_ROOT / "metabolomics-snets-v2" / "tools" / "metabolomicsnetsv2"
SCRIPTS = WORKFLOW_BASE / "scripts"
BINARIES = WORKFLOW_BASE / "binaries"


def run(job: "Job") -> bool:
    p = job.params
    input_dir = job.input_dir
    out = job.output_dir

    # Intermediate directories
    spectra_dir = out / "spectra"
    aligns_dir = out / "aligns"
    networking_params_dir = out / "networking_parameters"
    networking_pairs_dir = out / "networking_pairs"
    spectra_dir.mkdir(exist_ok=True)
    aligns_dir.mkdir(exist_ok=True)
    networking_params_dir.mkdir(exist_ok=True)
    networking_pairs_dir.mkdir(exist_ok=True)

    # Output file paths
    params_file = out / "specnetsparam.txt"
    clusterinfo_file = out / "clusterinfo.tsv"
    clusterinfo_summary = out / "clusterinfosummary.tsv"
    groupmapping_converted = out / "groupmapping_converted.tsv"
    attributemapping_converted = out / "attributemapping_converted.tsv"
    specs_ms_mgf = out / "specs_ms.mgf"
    networking_pairs_file = out / "networking_pairs_merged.tsv"
    networkedges_file = out / "networkedges.tsv"
    networkedges_selfloop = out / "networkedges_selfloop.tsv"
    networkedges_display = out / "networkedges_display.tsv"
    clusterinfo_enriched = out / "clusterinfo_enriched.tsv"
    graphml_file = out / "gnps_molecular_network.graphml"
    library_result = out / "library_search_results.tsv"
    library_result_db = out / "library_search_results_with_db.tsv"

    # Workflow parameters file (written from UI params)
    workflow_params_file = out / "workflowParameters.xml"
    _write_workflow_params(workflow_params_file, p)

    # Optional inputs (may not be provided)
    groupmapping_file = str(input_dir / "groupmapping.csv") if (input_dir / "groupmapping.csv").exists() else str(out / "empty_groupmapping.csv")
    attributemapping_file = str(input_dir / "attributemapping.csv") if (input_dir / "attributemapping.csv").exists() else str(out / "empty_attributemapping.csv")
    metadata_file = str(input_dir / "metadata.tsv") if (input_dir / "metadata.tsv").exists() else str(out / "empty_metadata.tsv")
    library_dir = str(input_dir / "library") if (input_dir / "library").is_dir() \
    else str(REPO_ROOT / "libraries")
    
    # Create empty optional files if needed
    _ensure_empty_file(out / "empty_groupmapping.csv")
    _ensure_empty_file(out / "empty_attributemapping.csv")
    _ensure_empty_file(out / "empty_metadata.tsv")

    python = sys.executable

    # ── Step 1: Generate MSCluster parameters ────────────────────────────────
    ok = job.run_step("specnets_param_gen", [
        python, str(SCRIPTS / "specnets_params_generator.py"),
        str(input_dir),
        str(workflow_params_file),
        str(params_file),
    ])
    if not ok:
        return False

    # ── Step 2: MSCluster spectral clustering ────────────────────────────────
    ok = job.run_step("msclustering", [
        python, str(SCRIPTS / "mscluster_wrapper.py"),
        str(input_dir),
        str(params_file),
        str(spectra_dir),
        str(aligns_dir),
        str(BINARIES / "main_specnets"),
    ])
    if not ok:
        return False

    # ── Step 3: Build clusterinfo + summary ──────────────────────────────────
    ok = job.run_step("clusterinfo", [
        str(BINARIES / "clusterinfo"),
        str(input_dir),
        str(spectra_dir),
        str(clusterinfo_file),
        str(clusterinfo_summary),
    ])
    if not ok:
        return False

    # ── Step 4: Metadata merge + group mapping convert ───────────────────────
    ok = job.run_step("metadata_merge", [
        python, str(SCRIPTS / "format_metadata.py"),
        str(workflow_params_file),
        str(input_dir),
        str(out / "metadata_merged"),
    ])
    # Non-fatal if no metadata provided
    if not ok:
        job.log("WARNING: metadata merge failed (no metadata provided?), continuing")

    ok = job.run_step("groupmapping_convert", [
        python, str(SCRIPTS / "convert_groupmapping.py"),
        str(workflow_params_file),
        groupmapping_file,
        attributemapping_file,
        str(out / "metadata_merged"),
        str(input_dir),
        str(groupmapping_converted),
        str(attributemapping_converted),
    ])
    if not ok:
        job.log("WARNING: group mapping convert failed, using empty mapping")
        _ensure_empty_file(groupmapping_converted)
        _ensure_empty_file(attributemapping_converted)

    # ── Step 5: Filter + copy spectra to specs_ms.mgf ────────────────────────
    ok = job.run_step("copy_filter_spectra", [
        python, str(SCRIPTS / "copy_filter_spectra.py"),
        str(spectra_dir),
        str(specs_ms_mgf),
        str(groupmapping_converted),
        str(clusterinfo_file),
        "-filterg6", p.get("FILTER_G6_BLANKS", "0"),
    ])
    if not ok:
        return False

    # ── Step 6: Prepare networking parameters (splits into N chunks) ─────────
    ok = job.run_step("prep_networking_params", [
        python, str(SCRIPTS / "prep_molecular_networking_parameters.py"),
        str(specs_ms_mgf),
        str(workflow_params_file),
        str(networking_params_dir),
        "-parallelism", "1",  # single-user: no parallelism needed
    ])
    if not ok:
        return False

    # ── Step 7: Molecular networking pairs (run each chunk sequentially) ──────
    param_files = sorted(networking_params_dir.glob("*.params"))
    if not param_files:
        param_files = sorted(networking_params_dir.glob("*"))

    job.log(f"Running networking on {len(param_files)} chunk(s)")
    for i, pf in enumerate(param_files):
        pairs_out = networking_pairs_dir / f"pairs_{i}.aligns"
        ok = job.run_step(f"networking_pairs_{i}", [
            python, str(SCRIPTS / "molecular_networking_parallel_step_wrapper.py"),
            str(pf),
            str(pairs_out),
            str(specs_ms_mgf),
            str(BINARIES / "main_execmodule"),
        ])
        if not ok:
            return False

    # ── Step 8: Merge pairs TSVs ─────────────────────────────────────────────
    ok = job.run_step("merge_pairs", [
        python, str(SCRIPTS / "merge_tsv_files_efficient.py"),
        str(networking_pairs_dir),
        str(networking_pairs_file),
    ])
    if not ok:
        return False

    # ── Step 9: Filter networking edges ──────────────────────────────────────
    ok = job.run_step("filter_edges", [
        python, str(SCRIPTS / "filter_networking_edges.py"),
        str(workflow_params_file),
        str(networking_pairs_file),
        str(networkedges_file),
    ])
    if not ok:
        return False

    # ── Step 10: Enrich clusterinfo summary ──────────────────────────────────
    ok = job.run_step("enrich_clusterinfo", [
        python, str(SCRIPTS / "AddAttributes.py"),
        str(workflow_params_file),
        str(clusterinfo_file),
        str(clusterinfo_summary),
        str(groupmapping_converted),
        str(attributemapping_converted),
        str(clusterinfo_enriched),
    ])
    if not ok:
        job.log("WARNING: clusterinfo enrichment failed, using plain summary")
        shutil.copy(clusterinfo_summary, clusterinfo_enriched)

    # ── Step 11: Add component index ─────────────────────────────────────────
    ok = job.run_step("add_component_index", [
        python, str(SCRIPTS / "Add_Component_Index.py"),
        str(networkedges_file),
        str(clusterinfo_enriched),
        str(clusterinfo_enriched),  # in-place update
    ])
    if not ok:
        job.log("WARNING: add component index failed, continuing")

    # ── Step 12: Network edges display (selfloop + display variants) ─────────
    ok = job.run_step("network_edges_display", [
        python, str(SCRIPTS / "add_self_loop.py"),
        str(workflow_params_file),
        str(clusterinfo_enriched),
        str(networkedges_file),
        str(networkedges_selfloop),
        str(networkedges_display),
        str(out / "networkedges_display_pairs.tsv"),
    ])
    if not ok:
        job.log("WARNING: network edges display failed, copying raw edges")
        shutil.copy(networkedges_file, networkedges_selfloop)

    # ── Step 13: Library search (if library available) ────────────────────────
    lib_path = Path(library_dir)
    if lib_path.exists() and any(lib_path.iterdir()):
        job.log("Library found, running library search")
        lib_params_dir = out / "libsearch_params"
        lib_params_dir.mkdir(exist_ok=True)
        lib_results_dir = out / "libsearch_results"
        lib_results_dir.mkdir(exist_ok=True)

        ok = job.run_step("library_search_param_gen", [
            str(BINARIES / "main_execmodule"),
            "LibrarySearchParamGeneration",
            str(specs_ms_mgf),
            str(workflow_params_file),
            str(lib_path),
            str(lib_params_dir),
        ])
        if ok:
            for i, lpf in enumerate(sorted(lib_params_dir.glob("*"))):
                ok = job.run_step(f"library_search_{i}", [
                    str(BINARIES / "main_execmodule"),
                    str(lpf),
                    str(lib_results_dir / f"result_{i}.tsv"),
                    str(specs_ms_mgf),
                    str(lib_path),
                ])
            job.run_step("library_search_merge", [
                python, str(SCRIPTS / "merge_tsv_files_efficient.py"),
                str(lib_results_dir),
                str(library_result),
            ])
            job.run_step("library_search_db_annot", [
                python, str(SCRIPTS / "add_db_annotations.py"),
                str(library_result),
                str(library_result_db),
            ])
    else:
        job.log("No library found, skipping library search")
        _ensure_empty_file(library_result_db)

    # ── Step 14: Convert to GraphML ───────────────────────────────────────────
    ok = job.run_step("convert_graphml", [
        python, str(SCRIPTS / "convert_networks_to_graphml.py"),
        str(networkedges_selfloop),
        str(clusterinfo_enriched),
        str(library_result_db),
        str(graphml_file),
    ])
    if not ok:
        return False

    job.log(f"Key outputs:")
    job.log(f"  GraphML network:   {graphml_file}")
    job.log(f"  Cluster info:      {clusterinfo_enriched}")
    job.log(f"  Network edges:     {networkedges_selfloop}")
    job.log(f"  Library results:   {library_result_db}")
    return True


def _write_workflow_params(path: Path, params: dict):
    """Write a simple XML params file matching what the scripts expect."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<parameters>"]
    for k, v in params.items():
        if v is None or str(v).strip() == "":
            continue
        # Escape XML special characters in both name attribute and text value
        lines.append(f'  <parameter name={quoteattr(k)}>{escape(str(v))}</parameter>')
    lines.append("</parameters>")
    path.write_text("\n".join(lines))


def _ensure_empty_file(path: Path):
    if not path.exists():
        path.touch()