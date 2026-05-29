"""
Feature-Based Molecular Networking (FBMN) workflow.
Translates fbmn_flow.xml + fbmn_tool.xml into sequential subprocess calls.

Core pipeline (from flow.xml):
  1. input_validation          - validate quantification table + MGF
  2. reformat_quantification   - format feature table (MZmine/XCMS/MS-DIAL/etc.)
  3. filter_spectra             - filter MGF (precursor window, noise)
  4. prep_molecular_networking_parameters
  5. molecular_networking_parallel_step (x N, run sequentially here)
  6. merge_networking_tsv_files
  7. filter_networking_edges
  8. prep_molecular_librarysearch_parameters
  9. molecular_librarysearch_parallel_step (x N)
 10. merge_librarysearch_tsv_files
 11. molecular_librarysearch_get_dbannotations
 12. clusterinfosummary_creation
 13. enrich_clusterinfo_summary
 14. metabolomics_convert_graphml

Skipped (external dependencies / optional):
  - run_dereplicator   (calls gnps.ucsd.edu — network dependency)
  - run_qiime2         (separate conda env, optional stats)
  - metabolomic_feature_statistics (optional plots)
  - metabolomics_convert_graphml_iin_collapse (IIN variant, optional)
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator import Job

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_BASE = REPO_ROOT / "feature-based-molecular-networking" / "tools" / "feature-based-molecular-networking"
SCRIPTS = WORKFLOW_BASE / "scripts"
BINARIES = WORKFLOW_BASE / "binaries"
LIBSEARCH_BINARY = REPO_ROOT / "molecular-librarysearch-v2" / "tools" / "molecularsearch" / "main_execmodule.allcandidates"
LOCAL_ANNOT_SCRIPT = Path(__file__).parent / "getGNPS_library_annotations_local.py"

def run(job: "Job") -> bool:
    import sys
    p = job.params
    input_dir = job.input_dir
    out = job.output_dir

    python = sys.executable

    # Merge in defaults that scripts expect but UI may not supply
    defaults = {
        "FILTER_LIBRARY": "1",
        "MIN_PEAK_INT": "0.0",
        "MAX_SHIFT_MASS": "100.0",
        "GROUP_COUNT_AGGREGATE_METHOD": "Mean",
        "TOP_K_RESULTS": "1",
        "RUN_DEREPLICATOR": "0",
        "PAIRS_MIN_COSINE": p.get("PAIRS_MIN_COSINE", "0.1"),
        "MAXIMUM_COMPONENT_SIZE": p.get("MAX_COMPONENT_SIZE", "100"),
        "tolerance.Ion_tolerance": p.get("TOLERANCE_ION", "0.02"),
        "tolerance.PM_tolerance": p.get("TOLERANCE_PM", "0.02"),
        "workflow": "FEATURE-BASED-MOLECULAR-NETWORKING",
        "workflow_version": "local",
        "task": "local",
        "user": "local",
    }
    for k, v in defaults.items():
        p.setdefault(k, v)

    # Locate required inputs
    all_inputs = sorted(input_dir.iterdir())
    job.log(f"Input dir contents ({len(all_inputs)} items): {[f.name for f in all_inputs]}")

    mgf_file = _find_input(input_dir, ["*.mgf", "*.MGF"])
    quant_table = _find_input(
        input_dir,
        ["*.csv", "*.CSV", "*.txt", "*.TXT", "*.tsv", "*.TSV"],
        exclude="metadata",
        exclude_also=["*.mgf", "*.MGF"],
    )
    metadata_file = _find_input(input_dir, ["*metadata*", "*metadata*.tsv", "*metadata*.txt"])
    library_dir = Path(os.environ.get("GNPS_LIBRARIES_DIR", REPO_ROOT / "libraries"))
    
    if not mgf_file:
        job.log("ERROR: No MGF file found in input directory")
        return False
    if not quant_table:
        job.log("ERROR: No quantification table found in input directory")
        return False

    job.log(f"MGF: {mgf_file}")
    job.log(f"Quantification table: {quant_table}")
    job.log(f"Metadata: {metadata_file or 'none'}")

    # reformat_quantification.py globs input_spectra_folder/* and requires exactly 1 file
    # Move MGF into its own subfolder so other input files don't interfere
    spectra_input_dir = input_dir / "spectra"
    spectra_input_dir.mkdir(exist_ok=True)
    import shutil as _shutil
    mgf_dest = spectra_input_dir / mgf_file.name
    if not mgf_dest.exists():
        _shutil.copy2(str(mgf_file), str(mgf_dest))
    job.log(f"Spectra input dir: {spectra_input_dir}")

    # Intermediate paths
    networking_params_dir = out / "networking_parameters"
    networking_pairs_dir = out / "networking_pairs"
    libsearch_params_dir = out / "libsearch_params"
    libanalogsearch_params_dir = out / "libanalogsearch_params"
    libsearch_results_dir = out / "libsearch_results"
    libanalogsearch_results_dir = out / "libanalogsearch_results"
    metadata_merged_dir = out / "metadata_merged"

    for d in [networking_params_dir, networking_pairs_dir, libsearch_params_dir,
              libanalogsearch_params_dir, libsearch_results_dir,
              libanalogsearch_results_dir, metadata_merged_dir]:
        d.mkdir(exist_ok=True)

    # Output files
    workflow_params_file = out / "workflowParameters.xml"
    quant_reformatted = out / "quantification_table_reformatted.csv"
    spectra_reformatted = out / "spectra_reformatted.mgf"
    spectra_filtered = out / "specs_ms_filtered.mgf"
    spectra_unfiltered = out / "specs_ms_unfiltered.mgf"
    networking_pairs_file = out / "networking_pairs_merged.tsv"
    networking_pairs_filtered = out / "networking_pairs_filtered.tsv"
    networkedges_legacy = out / "networkedges_legacy.tsv"
    networkedges_selfloop = out / "networkedges_selfloop.tsv"
    networkedges_display = out / "networkedges_display.tsv"
    libsearch_merged = out / "librarysearch_results.tsv"
    libsearch_db = out / "librarysearch_results_db.tsv"
    libanalogsearch_merged = out / "libanalogsearch_results.tsv"
    libanalogsearch_db = out / "libanalogsearch_results_db.tsv"
    clusterinfo_summary = out / "clusterinfo_summary.tsv"
    clusterinfo_enriched = out / "clusterinfo_summary_enriched.tsv"
    components_table = out / "components_table.tsv"
    graphml_file = out / "gnps_molecular_network.graphml"
    validation_dir = out / "validation_summary"
    validation_dir.mkdir(exist_ok=True)

    _write_workflow_params(workflow_params_file, p)

    # ── Step 1: Metadata merge ────────────────────────────────────────────────
    meta_input = str(metadata_file) if metadata_file else str(out / "empty_metadata")
    (out / "empty_metadata").mkdir(exist_ok=True)
    job.run_step("metadata_merge", [
        python, str(SCRIPTS / "format_metadata.py"),
        str(workflow_params_file),
        meta_input,
        str(metadata_merged_dir),
    ])
    # Non-fatal

    # ── Step 2: Input validation ──────────────────────────────────────────────
    ok = job.run_step("input_validation", [
        python, str(SCRIPTS / "input_validation.py"),
        str(workflow_params_file),
        str(quant_table),
        str(input_dir),
        str(metadata_merged_dir),
    ])
    if not ok:
        job.log("WARNING: input validation reported issues, continuing")

    # ── Step 3: Reformat quantification table ─────────────────────────────────
    ok = job.run_step("reformat_quantification", [
        python, str(SCRIPTS / "reformat_quantification.py"),
        p.get("QUANT_TABLE_SOURCE", "mzmine2").upper(),
        str(quant_table),
        str(quant_reformatted),
        str(spectra_input_dir),
        str(spectra_reformatted),
        str(workflow_params_file),
        "--QUANT_FILE_NORM", p.get("QUANT_FILE_NORM", "None"),
    ])
    if not ok:
        return False

    # ── Step 4: Filter spectra ────────────────────────────────────────────────
    ok = job.run_step("filter_spectra", [
        python, str(SCRIPTS / "filter_spectra.py"),
        str(spectra_reformatted),
        str(spectra_unfiltered),
        str(spectra_filtered),
        "--FILTER_PRECURSOR_WINDOW", p.get("FILTER_PRECURSOR_WINDOW", "1"),
        "--WINDOW_FILTER", p.get("WINDOW_FILTER", "1"),
    ])
    if not ok:
        return False

    # ── Step 5: Prep networking parameters ───────────────────────────────────
    ok = job.run_step("prep_networking_params", [
        python, str(SCRIPTS / "prep_molecular_networking_parameters.py"),
        str(spectra_filtered),
        str(workflow_params_file),
        str(networking_params_dir),
        "--parallelism", "1",
    ])
    if not ok:
        return False

    # ── Step 6: Molecular networking pairs ───────────────────────────────────
    param_files = sorted(networking_params_dir.glob("*"))
    job.log(f"Running networking on {len(param_files)} chunk(s)")
    for i, pf in enumerate(param_files):
        pairs_out = networking_pairs_dir / f"pairs_{i}.aligns"
        ok = job.run_step(f"networking_pairs_{i}", [
            python,
            str(Path(REPO_ROOT) / "metabolomics-snets-v2" / "tools" / "metabolomicsnetsv2" / "scripts" / "molecular_networking_parallel_step_wrapper.py"),
            str(pf),
            str(pairs_out),
            str(spectra_filtered),
            str(BINARIES / "main_execmodule"),
        ])
        if not ok:
            return False

    # ── Step 7: Merge pairs ───────────────────────────────────────────────────
    ok = job.run_step("merge_pairs", [
        python, str(SCRIPTS / "merge_tsv_files_efficient.py"),
        str(networking_pairs_dir),
        str(networking_pairs_file),
    ])
    if not ok:
        return False

    # ── Step 8: Filter networking edges ──────────────────────────────────────
    ok = job.run_step("filter_edges", [
        python, str(SCRIPTS / "filter_networking_edges.py"),
        str(workflow_params_file),
        str(networking_pairs_file),
        str(networking_pairs_filtered),
        str(networkedges_legacy),
    ])
    if not ok:
        return False

    # ── Step 10: Library search ───────────────────────────────────────────────
    if not LIBSEARCH_BINARY.exists():
        job.log(f"WARNING: {LIBSEARCH_BINARY} not found — skipping library search")
    elif library_dir.exists() and any(f.is_file() for f in library_dir.rglob("*")):
        job.log(f"Running library search (binary: {LIBSEARCH_BINARY})")
        ok = job.run_step("library_search_prep", [
            python, str(SCRIPTS / "prep_molecular_librarysearch_parameters.py"),
            str(library_dir),
            str(workflow_params_file),
            str(libsearch_params_dir),
            str(libanalogsearch_params_dir),
            "--parallelism", "1",
        ])

        if ok:
            any_search_ok = False
            for i, lpf in enumerate(sorted(libsearch_params_dir.glob("*"))):
                result_out = libsearch_results_dir / f"result_{i}.tsv"
                step_ok = job.run_step(f"library_search_{i}", [
                    str(LIBSEARCH_BINARY),
                    "ExecSpectralLibrarySearchMolecular",
                    str(lpf),
                    "-ccms_results_dir", str(result_out),
                    "-ccms_searchspectra_name", str(spectra_filtered),
                    "-ll", "9",
                ])
                if step_ok:
                    any_search_ok = True
            if any_search_ok:
                job.run_step("merge_libsearch", [
                    python, str(SCRIPTS / "merge_tsv_files_efficient.py"),
                    str(libsearch_results_dir),
                    str(libsearch_merged),
                ])
                merged_lines = libsearch_merged.read_text(errors="replace").splitlines() \
                    if libsearch_merged.exists() else []
                if len(merged_lines) > 1:
                    annot_script = LOCAL_ANNOT_SCRIPT if LOCAL_ANNOT_SCRIPT.exists() \
                        else SCRIPTS / "getGNPS_library_annotations.py"
                    job.log(f"Running annotation via {annot_script.name}")
                    annot_ok = job.run_step("libsearch_db_annot", [
                        python, str(annot_script),
                        str(libsearch_merged),
                        str(libsearch_db),
                        "--topk", p.get("TOP_K_RESULTS", "1"),
                    ], timeout=300)
                    if not annot_ok:
                        job.log("WARNING: libsearch_db_annot failed/timed out — writing stub so pipeline can continue")
                        _ensure_empty_libsearch_tsv(libsearch_db)
                else:
                    job.log("Library search produced no hits — writing empty results stub")
                    _ensure_empty_libsearch_tsv(libsearch_db)
            else:
                job.log("All library search steps failed - writing stub")
    else:
        job.log("No library found, ensure library files are flat and are not nested in subdirectories, skipping library search")

    for _lsdb in (libsearch_db, libanalogsearch_db):
        if not _lsdb.exists() or len(_lsdb.read_text(errors="replace").splitlines()) <= 1:
            _ensure_empty_libsearch_tsv(_lsdb)

    # ── Step 11: Clusterinfo summary creation ─────────────────────────────────
    ok = job.run_step("clusterinfosummary_for_featurenetworks", [
        python, str(SCRIPTS / "clusterinfosummary_for_featurenetworks.py"),
        str(workflow_params_file),
        str(quant_reformatted),
        str(metadata_merged_dir),
        str(spectra_filtered),
        str(clusterinfo_summary),
    ])
    if not ok:
        return False

    # ── Step 9: Network edges display ────────────────────────────────────────
    job.run_step("network_edges_display", [
        python, str(SCRIPTS / "create_network_edges_outputformatting.py"),
        str(workflow_params_file),
        str(spectra_filtered),
        str(clusterinfo_summary),   # placeholder, filled after step 12
        str(networkedges_legacy),
        str(networkedges_selfloop),
        str(networkedges_display),
        str(out / "networkedges_display_pairs.tsv"),
    ])
    # Step 9 depends on 11, hence moved
    # Non-fatal

    # ── Step 12: Enrich clusterinfo summary ───────────────────────────────────
    ok = job.run_step("enrich_clusterinfo", [
        python, str(SCRIPTS / "enrich_clusterinfosummary.py"),
        str(workflow_params_file),
        str(clusterinfo_summary),
        str(networking_pairs_filtered),
        str(libsearch_db),
        str(clusterinfo_enriched),
        str(components_table),
    ])
    if not ok:
        return False

    # ── Step 13: Convert to GraphML ───────────────────────────────────────────
    ok = job.run_step("convert_graphml", [
        python, str(SCRIPTS / "convert_networks_to_graphml.py"),
        str(networkedges_selfloop),
        str(clusterinfo_enriched),
        str(libsearch_db),
        str(graphml_file),
        "--input_analoglibrarysearch", str(libanalogsearch_db),
    ])
    if not ok:
        return False
    
    # ── Step 14: Molecular Community Networking (optional) ───────────────────
    if p.get("MOLECULAR_COMMUNITY_NETWORKING") == "1":
        from workflows import mcn as mcn_module

        class _MCNJob:
            def __init__(self):
                self.params = {
                    "MCN_K": p.get("MCN_K", "20"),
                    "MCN_C": p.get("MCN_C", "0.75"),
                    "MCN_SEED": p.get("MCN_SEED", "123"),
                }
                self.input_dir = out
                self.output_dir = out
                self.log = job.log

        ok = mcn_module.run(_MCNJob())
        if not ok:
            return False

        job.log(f"MCN outputs written to: {out / 'mcn'}")

    job.log("Key outputs:")
    job.log(f"  GraphML network:            {graphml_file}")
    if p.get("MOLECULAR_COMMUNITY_NETWORKING") == "1":
        job.log(f"  MCN full graph:             {out / 'gnps_mcn.graphml'}")
        job.log(f"  MCN cut graph:              {out / 'gnps_mcn_cut.graphml'}")
        job.log(f"  MCN spanning tree:          {out / 'gnps_mcn_spanning_tree.graphml'}")
    job.log(f"  Clusterinfo enriched:       {clusterinfo_enriched}")
    job.log(f"  Network edges:              {networkedges_selfloop}")
    job.log(f"  Library search results:     {libsearch_db}")
    job.log(f"  Quant table reformatted:    {quant_reformatted}")
    return True


def _find_input(directory: Path, patterns: list, exclude: str = None, exclude_also: list = None) -> Path | None:
    import fnmatch
    candidates = list(directory.iterdir())
    for pattern in patterns:
        for f in candidates:
            if not f.is_file():
                continue
            # Case-insensitive glob match
            if not fnmatch.fnmatch(f.name.lower(), pattern.lower()):
                continue
            if exclude and exclude.lower() in f.name.lower():
                continue
            if exclude_also:
                skip = False
                for xpat in exclude_also:
                    if fnmatch.fnmatch(f.name.lower(), xpat.lower()):
                        skip = True
                        break
                if skip:
                    continue
            return f
    return None


def _write_workflow_params(path: Path, params: dict):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<parameters>"]
    for k, v in params.items():
        # xmltodict parse_xml_file crashes on empty #text — skip blank values
        if v is None or str(v).strip() == "":
            continue
        lines.append(f'  <parameter name="{k}">{v}</parameter>')
    lines.append("</parameters>")
    path.write_text("\n".join(lines))


_LIBSEARCH_HEADER = "\t".join([
    "#Scan#", "SpectrumFile", "LibraryName", "MirrorLibraryName", "SpectrumID",
    "Title", "Compound_Name", "Retention_Time", "MZErrorPPM", "SMILES", "InChI",
    "InChIKey", "FormulaString", "IonMode", "Adduct", "ExactMass", "Precursor_MZ",
    "SharedPeaks", "TotalPeaks", "MatchingScore", "NumPeaks",
    
    # API-named aliases read by enrich_clusterinfosummary.py
    "MQScore", "Smiles", "INCHI",

    # Additional columns read by molecular_network_filtering_library.py
    "MassDiff", "tags", "Library_Class", "Instrument",
    "Ion_Source", "PI", "Data_Collector", "Compound_Source",
])
def _ensure_empty_libsearch_tsv(path: Path):
    """Write a valid header-only library search TSV. Always overwrites to prevent
    stale/corrupt content from a prior failed run causing downstream KeyError crashes."""
    path.write_text(_LIBSEARCH_HEADER + "\n")

def _ensure_empty_file(path: Path):
    if not path.exists():
        if "librarysearch" in path.name:
            _ensure_empty_libsearch_tsv(path)
        else:
            path.touch()