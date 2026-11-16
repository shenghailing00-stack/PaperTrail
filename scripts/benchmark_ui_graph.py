"""Offline TASK-004.1 graph timing; measurements, not machine-dependent CI gates."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import networkx as nx
import plotly
from papertrail.ui_service import network_figure


def synthetic_graph(size):
    """Three seeds, each candidate linked to all three; alternating directions."""
    graph = nx.DiGraph()
    for index in range(size):
        graph.add_node(f"{index:040x}", is_seed=index < 3, title=f"Synthetic paper {index}")
    nodes = list(graph)
    graph.add_edges_from([(nodes[0], nodes[1]), (nodes[1], nodes[2])])
    for index in range(3, size):
        for seed in nodes[:3]:
            graph.add_edge(seed, nodes[index]) if index % 2 else graph.add_edge(nodes[index], seed)
    return graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error("Use at least two repeats; report the distribution, not a threshold.")
    source = inspect.getsource(network_figure)
    result = {"label": args.label, "python": platform.python_version(), "networkx": nx.__version__,
              "plotly": plotly.__version__, "function_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "scope": "spring_layout + Plotly figure construction; no browser or HTTP", "measurements": []}
    network_figure(synthetic_graph(6), 6)  # Exclude first-use/import overhead.
    for size in (100, 200):
        graph = synthetic_graph(size)
        elapsed = []
        for iteration in range(args.repeats):
            start = perf_counter()
            figure, visible = network_figure(graph, size)
            elapsed.append(perf_counter() - start)
            assert len(visible) == size and len(figure.layout.annotations) == graph.number_of_edges()
            print(f"{args.label}: nodes={size}, repeat={iteration + 1}, seconds={elapsed[-1]:.6f}", flush=True)
        result["measurements"].append({"nodes": size, "edges": graph.number_of_edges(),
                                        "seconds": elapsed, "median_seconds": statistics.median(elapsed),
                                        "min_seconds": min(elapsed), "max_seconds": max(elapsed)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    # Keep the exact measured implementation beside the local timing evidence.
    args.output.with_suffix(".function.txt").write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
