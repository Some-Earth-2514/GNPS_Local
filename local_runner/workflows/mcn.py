"""
Molecular Community Networking (MCN)
Adaptive version: Works as a pipeline step or a standalone workflow.
"""

import sys
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np

if TYPE_CHECKING:
    from orchestrator import Job

def _sigmoid(x, k=20.0, c=0.75):
    """Sigmoid transformation for edge weights."""
    return 1 / (1 + np.exp(-k * (x - c)))

def _find_graphml_input(directory: Path) -> Path | None:
    """Finds the first .graphml file in the directory, regardless of name."""
    for f in directory.iterdir():
        if f.is_file() and fnmatch.fnmatch(f.name.lower(), "*.graphml"):
            return f
    return None

def run(job: "Job") -> bool:
    """
    Core entry point. 
    Standalone mode: Looks for any .graphml in input_dir.
    Pipeline mode: Looks for 'gnps_molecular_network.graphml' in output_dir.
    """
    p = job.params
    input_dir = job.input_dir
    out = job.output_dir
    
    # ── Input Resolution ──────────────────────────────────────────────────────
    # 1. Check if we are in a pipeline (input exists in output folder from previous step)
    input_graphml = out / "gnps_molecular_network.graphml"
    
    # 2. If not found, check the input directory (Standalone mode)
    if not input_graphml.exists():
        job.log("Standard pipeline input not found. Searching input directory for any GraphML...")
        input_graphml = _find_graphml_input(input_dir)

    if not input_graphml or not input_graphml.exists():
        job.log("ERROR: No .graphml file found in input or output directories.")
        return False

    job.log(f"MCN: Using input file: {input_graphml.name}")

    # ── Configuration ─────────────────────────────────────────────────────────
    mcn_out_dir = out / "mcn"
    mcn_out_dir.mkdir(exist_ok=True)

    k_val = float(p.get("MCN_K", 20.0))
    c_val = float(p.get("MCN_C", 0.75))
    seed  = int(p.get("MCN_SEED", 123))
    base_name = p.get("JOB_NAME") or job.id

    # ── Step: load_graph ──────────────────────────────────────────────────────
    job.log("--- STEP: mcn_load_graph ---")
    try:
        G = nx.read_graphml(str(input_graphml))
        job.log(f"MCN: Nodes={len(G.nodes())}, Edges={len(G.edges())}")
        if len(G.nodes()) == 0:
            job.log("ERROR: Graph contains no nodes.")
            job.log("STEP FAILED (exit 1)")
            return False
        job.log("STEP OK")
    except Exception as e:
        job.log(f"ERROR: {e}")
        job.log("STEP FAILED (exit 1)")
        return False

    # ── Step: apply_sigmoid ───────────────────────────────────────────────────
    job.log("--- STEP: mcn_apply_sigmoid ---")
    try:
        for u, v, data in G.edges(data=True):
            score = data.get("cosine_score") or data.get("weight") or 0.0
            data["sigmoid_score"] = _sigmoid(float(score), k_val, c_val)

        gcc_nodes = max(nx.connected_components(G), key=len)
        Gc = G.subgraph(gcc_nodes).copy()
        job.log(f"MCN: Processing GCC ({len(Gc.nodes())} nodes)")
        job.log("STEP OK")
    except Exception as e:
        job.log(f"ERROR: {e}")
        job.log("STEP FAILED (exit 1)")
        return False

    # ── Step: louvain_communities ─────────────────────────────────────────────
    job.log("--- STEP: mcn_louvain_communities ---")
    try:
        comm = nx.community.louvain_communities(Gc, seed=seed, weight="sigmoid_score")
        job.log(f"MCN: Detected {len(comm)} communities")

        for ind, community in enumerate(comm):
            for node in community:
                Gc.nodes[node]["community_id"] = ind + 1

        # Singleton logic
        for node in Gc:
            is_singleton = all(
                float(Gc.edges[node, nbr].get("cosine_score", 0)) < 0.7
                for nbr in Gc.neighbors(node)
            )
            Gc.nodes[node]["singleton"] = "1" if is_singleton else "0"

        job.log("STEP OK")
    except Exception as e:
        job.log(f"ERROR: {e}")
        job.log("STEP FAILED (exit 1)")
        return False

    # ── Step: write_outputs ───────────────────────────────────────────────────
    job.log("--- STEP: mcn_write_outputs ---")
    try:
        nx.write_graphml(Gc, str(mcn_out_dir / f"{base_name}.graphml"))

        # Intra-community cut
        Gc_cut = nx.Graph(Gc)
        for u, v in list(Gc.edges()):
            if Gc.nodes[u]["community_id"] != Gc.nodes[v]["community_id"]:
                Gc_cut.remove_edge(u, v)
        nx.write_graphml(Gc_cut, str(mcn_out_dir / f"{base_name}_cut.graphml"))

        # Spanning tree
        St = nx.maximum_spanning_tree(Gc_cut, weight="sigmoid_score")
        nx.write_graphml(St, str(mcn_out_dir / f"{base_name}_spanning_tree.graphml"))

        # Copy to root output for easy download
        import shutil as _shutil
        for mcn_file in mcn_out_dir.glob("*.graphml"):
            _shutil.copy2(str(mcn_file), str(out / mcn_file.name))

        job.log("MCN: Process completed.")
        job.log("STEP OK")
    except Exception as e:
        job.log(f"ERROR: {e}")
        job.log("STEP FAILED (exit 1)")
        return False

    return True

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--k", default=20.0, type=float)
    parser.add_argument("--c", default=0.75, type=float)
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir).parent
    mcn_out_dir = Path(args.output_dir)
    mcn_out_dir.mkdir(exist_ok=True)