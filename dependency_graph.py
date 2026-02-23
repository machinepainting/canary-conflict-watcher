#!/usr/bin/env python3
"""
Dependency Graph Builder

Standalone script to build and query the import/export dependency graph.
The main watcher uses this internally, but you can also run it directly
to inspect dependencies.

Usage:
    python dependency_graph.py --repo . --build          # Build/rebuild the graph
    python dependency_graph.py --repo . --query src/types/user.ts  # What depends on this file?
    python dependency_graph.py --repo . --reverse src/components/UserProfile.tsx  # What does this import?
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Import graph functions from main watcher
sys.path.insert(0, os.path.dirname(__file__))
from watch_conflicts import (
    build_dependency_graph, find_dependents, load_config
)


def query_dependents(dep_graph, filepath):
    """Find all files that depend on the given file (directly or transitively)."""
    dependents = find_dependents(filepath, dep_graph)

    direct = set()
    transitive = set()

    for source_file, imports in dep_graph.items():
        if filepath in imports:
            direct.add(source_file)

    transitive = dependents - direct

    return direct, transitive


def query_imports(dep_graph, filepath):
    """Find what the given file imports."""
    return dep_graph.get(filepath, [])


def load_cached_graph(repo_path):
    """Load the cached dependency graph."""
    cache_path = Path(repo_path) / ".canary-deps.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


def main():
    parser = argparse.ArgumentParser(description="Dependency Graph Tool")
    parser.add_argument("--repo", default=".", help="Path to repo")
    parser.add_argument("--build", action="store_true", help="Build/rebuild the dependency graph")
    parser.add_argument("--query", metavar="FILE", help="Find files that depend on FILE")
    parser.add_argument("--reverse", metavar="FILE", help="Find what FILE imports")
    parser.add_argument("--stats", action="store_true", help="Show graph statistics")
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo)
    config = load_config(repo_path)

    if args.build:
        print("Building dependency graph...")
        graph = build_dependency_graph(repo_path, config)
        total_files = len(graph)
        total_edges = sum(len(v) for v in graph.values())
        print(f"Done. {total_files} files, {total_edges} import relationships.")
        print(f"Cached at: {Path(repo_path) / '.canary-deps.json'}")
        return

    # Load existing graph
    graph = load_cached_graph(repo_path)
    if not graph:
        print("No cached graph found. Run with --build first.", file=sys.stderr)
        sys.exit(1)

    if args.query:
        direct, transitive = query_dependents(graph, args.query)
        print(f"\nFiles that depend on `{args.query}`:\n")
        if direct:
            print(f"  Direct ({len(direct)}):")
            for f in sorted(direct):
                print(f"    {f}")
        if transitive:
            print(f"\n  Transitive ({len(transitive)}):")
            for f in sorted(transitive):
                print(f"    {f}")
        if not direct and not transitive:
            print("  No dependents found.")

    elif args.reverse:
        imports = query_imports(graph, args.reverse)
        print(f"\nFiles imported by `{args.reverse}`:\n")
        if imports:
            for f in sorted(imports):
                print(f"  {f}")
        else:
            print("  No imports found (or file not in graph).")

    elif args.stats:
        total_files = len(graph)
        total_edges = sum(len(v) for v in graph.values())

        # Most depended-on files
        dep_count = {}
        for source, imports in graph.items():
            for imp in imports:
                dep_count[imp] = dep_count.get(imp, 0) + 1

        most_depended = sorted(dep_count.items(), key=lambda x: -x[1])[:10]

        # Most importing files
        most_imports = sorted(graph.items(), key=lambda x: -len(x[1]))[:10]

        print(f"\nDependency Graph Statistics:")
        print(f"  Files tracked: {total_files}")
        print(f"  Import relationships: {total_edges}")
        print(f"\n  Most depended-on files:")
        for f, count in most_depended:
            print(f"    {f} ({count} dependents)")
        print(f"\n  Files with most imports:")
        for f, imports in most_imports:
            print(f"    {f} ({len(imports)} imports)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
