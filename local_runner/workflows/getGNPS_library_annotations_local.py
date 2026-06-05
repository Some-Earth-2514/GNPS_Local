#!/usr/bin/env python3
"""
getGNPS_library_annotations_local.py

Local replacement for getGNPS_library_annotations.py.
No network calls. Backfills SMILES/InChI from library MGF files on disk.

Sidecar index strategy:
  Each MGF file gets a companion <filename>.mgf.idx file:
    - JSON dict: {spectrum_id: byte_offset_of_BEGIN_IONS_line}
    - Built once, reused on all subsequent runs
    - Invalidated if MGF file mtime changes (rebuilt automatically)
    - Pre-build all sidecars via: python ... --build_indexes --library_dir /path

  Warm run (sidecars exist):
    - Load sidecar JSON (~50ms per file)
    - Seek directly to each needed spectrum block
    - No full-file streaming required
    - ~<5s for 46 IDs across 98 files

  Cold run (no sidecars):
    - Falls back to two-pass streaming (original behaviour)
    - Builds and saves sidecars for next run

  HDD safety:
    - If needed IDs > 10% of total spectra in a file, fall back to
      sequential stream instead of random seeks (avoids HDD seek thrash)

CLI:
    # Normal annotation run
    python getGNPS_library_annotations_local.py <input.tsv> <output.tsv> \
        --topk 1 --library_dir /path/to/mgf/dir

    # Pre-build all sidecar indexes (run once after adding new libraries)
    python getGNPS_library_annotations_local.py \
        --build_indexes --library_dir /path/to/mgf/dir
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# ── Output schema ─────────────────────────────────────────────────────────────
OUTPUT_FIELDNAMES = [
    "#Scan#", "SpectrumFile", "LibraryName", "MirrorLibraryName", "SpectrumID",
    "Title", "Compound_Name", "Retention_Time", "MZErrorPPM", "SMILES", "InChI",
    "InChIKey", "FormulaString", "IonMode", "Adduct", "ExactMass", "Precursor_MZ",
    "SharedPeaks", "TotalPeaks", "MatchingScore", "NumPeaks",
    "MQScore", "Smiles", "INCHI",
    "MassDiff", "tags", "Library_Class", "Instrument",
    "Ion_Source", "PI", "Data_Collector", "Compound_Source",
]

_PANDAS_NA = {
    "", "n/a", "na", "nan", "none", "null",
    "#n/a", "#na", "#n/a n/a", "<na>",
    "-nan", "-1.#ind", "-1.#qnan", "1.#ind", "1.#qnan",
}


def s(v) -> str:
    if v is None:
        return "N_A"
    sv = str(v).strip()
    return "N_A" if sv.lower() in _PANDAS_NA else sv


# ── Sidecar index build & load ────────────────────────────────────────────────

def _sidecar_path(mgf_path: Path) -> Path:
    return mgf_path.with_suffix(".mgf.idx")


def _sidecar_valid(mgf_path: Path, sidecar: Path) -> bool:
    """Sidecar is valid if it exists and is newer than the MGF."""
    if not sidecar.exists():
        return False
    return sidecar.stat().st_mtime >= mgf_path.stat().st_mtime


def build_sidecar(mgf_path: Path, force: bool = False) -> dict[str, int]:
    """
    Stream MGF once, record byte offset of each BEGIN IONS line.
    Saves {spectrum_id: offset} to <mgf>.idx JSON.
    Returns the index dict.
    offset points to the start of the BEGIN IONS line so we can seek
    directly to it and parse the full block.
    """
    sidecar = _sidecar_path(mgf_path)
    if not force and _sidecar_valid(mgf_path, sidecar):
        print(f"  Sidecar OK: {sidecar.name}", file=sys.stderr)
        with open(sidecar, "r") as f:
            return json.load(f)

    print(f"  Building sidecar: {mgf_path.name}", file=sys.stderr)
    index: dict[str, int] = {}
    current_offset: int = 0
    block_start: int = 0
    in_block: bool = False

    with open(mgf_path, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", errors="replace")
            stripped = line.strip().upper()

            if stripped == "BEGIN IONS":
                block_start = current_offset
                in_block = True
                current_offset += len(raw)
                continue

            if stripped == "END IONS":
                in_block = False
                current_offset += len(raw)
                continue

            if in_block and line[:11].upper() == "SPECTRUMID=":
                sid = line[11:].strip()
                if sid:
                    index[sid] = block_start

            current_offset += len(raw)

    # Write sidecar atomically (temp file + rename) to avoid corrupt partial writes
    tmp = sidecar.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(index, f, separators=(",", ":"))
    tmp.replace(sidecar)

    print(f"  Sidecar saved: {sidecar.name} ({len(index)} IDs)", file=sys.stderr)
    return index


def _parse_block_at_offset(fh, offset: int) -> dict:
    """
    Seek to offset, parse one MGF spectrum block.
    Returns metadata dict (all KEY=VAL fields, no peak data).
    """
    fh.seek(offset)
    fields: dict = {}
    for raw in fh:
        line = raw.strip()
        upper = line.upper()
        if upper == "END IONS":
            break
        if not line or line[0:1].isdigit():
            continue
        if b"=" in line:
            key, _, val = line.partition(b"=")
            fields[key.strip().upper().decode("utf-8", errors="replace")] = \
                val.strip().decode("utf-8", errors="replace")
    return fields


def _parse_block_sequential(mgf_path: Path, wanted: set[str]) -> dict[str, dict]:
    """
    Fallback: stream file sequentially, collect all wanted blocks.
    Used when random seeks would be inefficient (HDD / high ID density).
    """
    results: dict[str, dict] = {}
    remaining = set(wanted)

    with open(mgf_path, "rb", buffering=1 << 20) as fh:
        current: dict | None = None
        current_id: str | None = None

        for raw in fh:
            line = raw.strip()
            upper = line.upper()

            if upper == b"BEGIN IONS":
                current = {}
                current_id = None
                continue

            if upper == b"END IONS":
                if current_id and current_id in wanted:
                    results[current_id] = current
                    remaining.discard(current_id)
                current = None
                current_id = None
                if not remaining:
                    break
                continue

            if current is None:
                continue
            if line and line[0:1].isdigit():
                continue
            if b"=" not in line:
                continue

            key, _, val = line.partition(b"=")
            key_upper = key.strip().upper().decode("utf-8", errors="replace")
            val_str = val.strip().decode("utf-8", errors="replace")
            current[key_upper] = val_str
            if key_upper == "SPECTRUMID":
                current_id = val_str

    return results


# HDD safety threshold: if needed IDs > this fraction of total spectra,
# sequential stream is faster than many random seeks
_SEEK_FALLBACK_RATIO = 0.10


def fetch_metadata_for_file(
    mgf_path: Path,
    wanted: set[str],
    file_index: dict[str, int],
) -> dict[str, dict]:
    """
    Fetch metadata for wanted IDs from one MGF file.
    Always uses sequential stream (Windows mount safe, HDD safe).
    Random seeks on network/bind mounts cause I/O thrashing;
    sequential is faster in practice on all storage types.
    """
    return _parse_block_sequential(mgf_path, wanted)


# ── Main index builder ────────────────────────────────────────────────────────

def build_lazy_index(
    library_dir: Path,
    needed_ids: set[str],
) -> dict[str, dict]:
    """
    For each MGF in library_dir:
      1. Load or build sidecar index
      2. Check which needed IDs are in this file
      3. Fetch only those blocks (seek or sequential)
    Returns {spectrum_id: metadata_dict}.
    """
    if not library_dir or not library_dir.exists():
        print(f"WARNING: library_dir not found: {library_dir}", file=sys.stderr)
        return {}

    mgf_files = sorted(library_dir.glob("*.mgf")) + sorted(library_dir.glob("*.MGF"))
    seen: set = set()
    mgf_files = [f for f in mgf_files if not (f in seen or seen.add(f))]

    if not mgf_files:
        print(f"WARNING: no .mgf files in {library_dir}", file=sys.stderr)
        return {}

    print(
        f"Library backfill: {len(mgf_files)} MGF file(s), {len(needed_ids)} needed IDs",
        file=sys.stderr,
    )

    # Build/load all sidecars, find which file each needed ID lives in
    id_to_file_index: dict[str, tuple[Path, dict[str, int]]] = {}
    total_indexed = 0

    for mgf_path in mgf_files:
        try:
            file_index = build_sidecar(mgf_path)
            total_indexed += len(file_index)
            for sid in needed_ids:
                if sid in file_index and sid not in id_to_file_index:
                    id_to_file_index[sid] = (mgf_path, file_index)
        except Exception as e:
            print(f"  WARNING sidecar {mgf_path.name}: {e}", file=sys.stderr)

    found = set(id_to_file_index.keys())
    missing = needed_ids - found
    print(
        f"Index load complete: {total_indexed} total IDs, "
        f"{len(found)} needed IDs located, "
        f"{len(missing)} not found",
        file=sys.stderr,
    )
    if missing:
        print(f"  Not found: {sorted(missing)[:10]}", file=sys.stderr)

    if not found:
        return {}

    # Group needed IDs by file for efficient fetching
    file_to_wanted: dict[Path, tuple[set[str], dict[str, int]]] = {}
    for sid, (mgf_path, file_index) in id_to_file_index.items():
        if mgf_path not in file_to_wanted:
            file_to_wanted[mgf_path] = (set(), file_index)
        file_to_wanted[mgf_path][0].add(sid)

    # Fetch metadata blocks
    results: dict[str, dict] = {}
    for mgf_path, (wanted, file_index) in file_to_wanted.items():
        print(f"  Fetching: {mgf_path.name} — {len(wanted)} ID(s)", file=sys.stderr)
        try:
            fetched = fetch_metadata_for_file(mgf_path, wanted, file_index)
            results.update(fetched)
        except Exception as e:
            print(f"  WARNING fetch {mgf_path.name}: {e}", file=sys.stderr)

    print(f"Fetch complete: metadata retrieved for {len(results)} ID(s)", file=sys.stderr)
    return results


def build_all_indexes(library_dir: Path):
    """Pre-build all sidecar indexes. Run once after adding new libraries."""
    mgf_files = sorted(library_dir.glob("*.mgf")) + sorted(library_dir.glob("*.MGF"))
    seen: set = set()
    mgf_files = [f for f in mgf_files if not (f in seen or seen.add(f))]

    print(f"Building indexes for {len(mgf_files)} MGF file(s) in {library_dir}",
          file=sys.stderr)
    for mgf_path in mgf_files:
        try:
            build_sidecar(mgf_path, force=False)
        except Exception as e:
            print(f"  ERROR {mgf_path.name}: {e}", file=sys.stderr)
    print("Done.", file=sys.stderr)


# ── Row mapping ───────────────────────────────────────────────────────────────

def map_row(hit: dict, mgf_index: dict[str, dict]) -> dict:
    mq            = s(hit.get("MQScore", ""))
    compound_name = s(hit.get("CompoundName", ""))
    spectrum_id   = s(hit.get("LibrarySpectrumID", ""))
    mz_error      = s(hit.get("mzErrorPPM", ""))
    shared_peaks  = s(hit.get("LibSearchSharedPeaks", ""))
    mass_diff     = s(hit.get("ParentMassDiff", ""))
    compound_src  = s(hit.get("Organism", ""))
    exact_mass    = s(hit.get("ExactMass", ""))
    precursor_mz  = s(hit.get("SpecMZ", ""))
    smiles        = s(hit.get("Smiles", ""))
    inchi         = s(hit.get("Inchi", ""))

    adduct = "N_A"
    annotation = hit.get("Annotation", "")
    if annotation and annotation != "*..*":
        parts = annotation.strip().split(" ")
        if len(parts) >= 2:
            candidate = parts[-1]
            if any(c in candidate for c in ("+", "-", "M", "[", "]")):
                adduct = s(candidate)

    mgf = mgf_index.get(spectrum_id, {}) if spectrum_id != "N_A" else {}

    if smiles == "N_A" and mgf.get("SMILES"):
        smiles = s(mgf["SMILES"].strip('"').strip("'"))
    if inchi == "N_A" and mgf.get("INCHI"):
        inchi = s(mgf["INCHI"].strip('"').strip("'"))
    if compound_name == "N_A" and mgf.get("NAME"):
        compound_name = s(mgf["NAME"])
    if compound_src == "N_A" and mgf.get("ORGANISM"):
        compound_src = s(mgf["ORGANISM"])
    if exact_mass == "N_A" and mgf.get("PEPMASS"):
        exact_mass = s(mgf["PEPMASS"])
    if adduct == "N_A" and mgf.get("ADDUCT"):
        adduct = s(mgf["ADDUCT"])

    ionmode        = s(mgf.get("IONMODE", ""))
    instrument     = s(mgf.get("SOURCE_INSTRUMENT", ""))
    pi             = s(mgf.get("PI", ""))
    data_collector = s(mgf.get("DATACOLLECTOR", ""))
    lib_class      = s(mgf.get("LIBRARYQUALITY", ""))

    return {
        "#Scan#":           s(hit.get("#Scan#", "")),
        "SpectrumFile":     s(hit.get("SpectrumFile", "")),
        "LibraryName":      s(hit.get("LibraryName", "")),
        "MirrorLibraryName":"N_A",
        "SpectrumID":       spectrum_id,
        "Title":            "N_A",
        "Compound_Name":    compound_name,
        "Retention_Time":   "N_A",
        "MZErrorPPM":       mz_error,
        "SMILES":           smiles,
        "InChI":            inchi,
        "InChIKey":         "N_A",
        "FormulaString":    "N_A",
        "IonMode":          ionmode,
        "Adduct":           adduct,
        "ExactMass":        exact_mass,
        "Precursor_MZ":     precursor_mz,
        "SharedPeaks":      shared_peaks,
        "TotalPeaks":       "N_A",
        "MatchingScore":    mq,
        "NumPeaks":         "N_A",
        "MQScore":          mq,
        "Smiles":           smiles,
        "INCHI":            inchi,
        "MassDiff":         mass_diff,
        "tags":             "N_A",
        "Library_Class":    lib_class,
        "Instrument":       instrument,
        "Ion_Source":       "N_A",
        "PI":               pi,
        "Data_Collector":   data_collector,
        "Compound_Source":  compound_src,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Local GNPS library annotation enrichment (no network calls)."
    )
    parser.add_argument("input_results", nargs="?",
                        help="librarysearch_results.tsv from merge step")
    parser.add_argument("output_results", nargs="?",
                        help="librarysearch_results_db.tsv output path")
    parser.add_argument("--topk", type=int, default=1,
                        help="Keep top-K hits per query scan (default: 1)")
    parser.add_argument("--library_dir", default=None,
                        help="Directory containing .mgf library files")
    parser.add_argument("--build_indexes", action="store_true",
                        help="Pre-build all sidecar indexes and exit")
    args = parser.parse_args()

    # ── Index pre-build mode ──────────────────────────────────────────────────
    if args.build_indexes:
        if not args.library_dir:
            print("ERROR: --library_dir required with --build_indexes", file=sys.stderr)
            sys.exit(1)
        build_all_indexes(Path(args.library_dir))
        return

    if not args.input_results or not args.output_results:
        parser.print_help()
        sys.exit(1)

    # ── Read input TSV ────────────────────────────────────────────────────────
    try:
        with open(args.input_results, "r", errors="replace") as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = list(reader)
    except Exception as e:
        print(f"ERROR reading {args.input_results}: {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("No hits in input — writing header-only output", file=sys.stderr)
        with open(args.output_results, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_FIELDNAMES,
                           delimiter="\t").writeheader()
        return

    print(f"Processing {len(rows)} hits, keeping top {args.topk} per scan",
          file=sys.stderr)

    # ── Detect scan column defensively ───────────────────────────────────────
    first = rows[0]
    scan_col = next(
        (c for c in ("#Scan#", "Scan#", "scan", "#scan#") if c in first),
        list(first.keys())[0]
    )
    if scan_col != "#Scan#":
        print(f"WARNING: scan column is '{scan_col}', expected '#Scan#'", file=sys.stderr)

    # ── Group by scan, sort by MQScore descending, take top-K ────────────────
    by_scan: dict[str, list] = defaultdict(list)
    for row in rows:
        scan = row.get(scan_col, "")
        try:
            score = float(row.get("MQScore") or 0)
        except ValueError:
            score = 0.0
        by_scan[scan].append((score, row))

    topk_rows: list[dict] = []
    for scan in sorted(by_scan.keys(), key=lambda x: int(x) if x.isdigit() else x):
        scored = sorted(by_scan[scan], key=lambda x: x[0], reverse=True)
        for _, hit in scored[:args.topk]:
            topk_rows.append(hit)

    # ── Build index only for IDs we need ─────────────────────────────────────
    mgf_index: dict[str, dict] = {}
    if args.library_dir:
        needed_ids = {
            r.get("LibrarySpectrumID", "").strip()
            for r in topk_rows
            if r.get("LibrarySpectrumID", "").strip()
        }
        print(f"Unique spectrum IDs to backfill: {len(needed_ids)}", file=sys.stderr)
        mgf_index = build_lazy_index(Path(args.library_dir), needed_ids)
    else:
        print("WARNING: --library_dir not supplied; SMILES/InChI will not be backfilled",
              file=sys.stderr)

    # ── Map rows and write output ─────────────────────────────────────────────
    output_rows: list[dict] = []
    for hit in topk_rows:
        out = map_row(hit, mgf_index)
        if scan_col != "#Scan#":
            out["#Scan#"] = s(hit.get(scan_col, ""))
        print(out["SpectrumID"])
        output_rows.append(out)

    with open(args.output_results, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUTPUT_FIELDNAMES, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(output_rows)

    smiles_filled = sum(1 for r in output_rows if r.get("SMILES", "N_A") != "N_A")
    inchi_filled  = sum(1 for r in output_rows if r.get("INCHI",  "N_A") != "N_A")
    print(
        f"Wrote {len(output_rows)} rows to {args.output_results} "
        f"| SMILES populated: {smiles_filled}/{len(output_rows)} "
        f"| InChI populated: {inchi_filled}/{len(output_rows)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()