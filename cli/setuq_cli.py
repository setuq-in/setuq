#!/usr/bin/env python3
"""Setuq CLI - Query Splunk using natural language."""

import argparse
import sys
import httpx


def get_base_url(args):
    import os
    return args.url or os.environ.get("SETUQ_API_URL", "http://localhost:8000")


def query(base_url: str, text: str):
    """Send a query and display results."""
    try:
        response = httpx.post(
            f"{base_url}/api/query",
            json={"query": text},
            timeout=60.0,
        )
    except httpx.ConnectError:
        print(f"Error: Cannot connect to Setuq engine at {base_url}")
        sys.exit(1)

    if response.status_code != 200:
        error = response.json()
        print(f"Error: {error.get('detail', 'Unknown error')}")
        return

    data = response.json()

    print(f"\nSPL: {data['spl']}")
    print(f"Results: {data['metadata']['result_count']} rows "
          f"({data['metadata']['execution_time_ms']}ms)")
    print()

    # Print results as table
    results = data["results"]
    if results:
        headers = list(results[0].keys())
        print("  ".join(h.ljust(20) for h in headers))
        print("  ".join("-" * 20 for _ in headers))
        for row in results[:20]:
            print("  ".join(str(row.get(h, "")).ljust(20) for h in headers))
        if len(results) > 20:
            print(f"  ... and {len(results) - 20} more rows")
        print()

    print(f"Summary: {data['summary']}")


def show_schema(base_url: str):
    """Display the current schema."""
    try:
        response = httpx.get(f"{base_url}/api/schema", timeout=10.0)
    except httpx.ConnectError:
        print(f"Error: Cannot connect to Setuq engine at {base_url}")
        sys.exit(1)

    data = response.json()
    for index_name, index_data in data.get("indexes", {}).items():
        print(f"\nIndex: {index_name}")
        for st_name, st_data in index_data.get("sourcetypes", {}).items():
            fields = st_data.get("fields", [])
            print(f"  Sourcetype: {st_name}")
            print(f"    Fields: {', '.join(fields)}")


def check_health(base_url: str):
    """Check engine health."""
    try:
        response = httpx.get(f"{base_url}/api/health", timeout=5.0)
        data = response.json()
        print(f"Engine status: {data['status']}")
    except httpx.ConnectError:
        print(f"Error: Cannot connect to Setuq engine at {base_url}")
        sys.exit(1)


def interactive_mode(base_url: str):
    """Run in interactive mode."""
    print("Setuq Interactive Mode (type 'exit' to quit)")
    print("-" * 45)
    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        query(base_url, user_input)


def main():
    parser = argparse.ArgumentParser(description="Setuq - Query Splunk with natural language")
    parser.add_argument("query", nargs="?", help="Natural language query")
    parser.add_argument("--url", help="Engine API URL (default: http://localhost:8000)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--schema", action="store_true", help="Show current schema")
    parser.add_argument("--health", action="store_true", help="Check engine health")

    args = parser.parse_args()
    base_url = get_base_url(args)

    if args.health:
        check_health(base_url)
    elif args.schema:
        show_schema(base_url)
    elif args.interactive:
        interactive_mode(base_url)
    elif args.query:
        query(base_url, args.query)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
