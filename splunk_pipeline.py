"""
Combined Splunk export + YAML generation pipeline.

Phase 1 (extraction): Connects to live Splunk via REST API and exports all
  config objects (saved searches, dashboards, indexes, props, transforms,
  lookups, macros, eventtypes, tags, datamodels, apps, users, roles, inputs,
  fieldaliases, calcfields, kvstore) to splunk_export/ as JSON files.

Phase 2 (YAML generation): Reads splunk_export/ JSON files and generates
  agent-ready YAMLs in splunk_metadata/:
    dashboards.yaml, <kind>.yaml (one per metadata kind),
    spl_patterns.yaml, dependencies.yaml

Phase 3 (schema overrides): Reads the Phase 2 splunk_metadata/ YAMLs and
  synthesises a generic schema_overrides.yaml — the structural/semantic schema
  the SOC agent feeds its planner + SPL generator (known indexes, sourcetypes,
  fields, pre-built eventtype filters, lookups, datamodels). Works for ANY
  standalone Splunk: nothing here is dataset-specific. Written by default to
  splunk_metadata/schema_overrides.yaml so it never clobbers a hand-curated
  engine/schema_overrides.yaml (which carries business semantics no config
  scan can reproduce). Point --schema-overrides-out at the engine file only
  when there is no curated layer to preserve.

All three phases run by default.
  --skip-extract          skip Phase 1; use existing splunk_export/ directory
  --skip-gen-yaml         skip Phase 2
  --skip-schema-overrides skip Phase 3
  --schema-overrides-out  output path for Phase 3 (default: <meta>/schema_overrides.yaml)
  --with-field-stats      (Phase 1 opt-in) also writes field_stats.yaml; Phase 3
                          uses it for per-field cardinality when present
"""

import csv
import hashlib
import json
import os
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from collections import OrderedDict, defaultdict

try:
    import yaml
except ImportError:
    print("PyYAML missing. pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# splunklib + python-dotenv are imported lazily in run_extraction() (Phase 1 only)
# so the offline phases (YAML generation, schema-overrides synthesis) run without
# a live Splunk SDK installed. `client`/`results` are bound there.
client = None
results = None


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Module-level globals initialised in main() so phase functions can reference
# them directly without needing extra parameters.
service = None      # splunklib connection (Phase 1)
BASE_DIR = None     # splunk_export/ directory (Phase 1 writes, Phase 2 reads)
SRC = None          # alias for BASE_DIR used by Phase 2 helpers
META_OUT_DIR = None # splunk_metadata/ directory (Phase 2 output)
DASH_OUT = None     # META_OUT_DIR/dashboards.yaml
SCHEMA_OVERRIDES_OUT = None  # schema_overrides.yaml output path (Phase 3)


# =========================================================
# CLI ARGS
# =========================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Export Splunk config/objects to JSON, then generate agent-ready YAMLs"
    )

    # --- connection (Phase 1) ---
    parser.add_argument("--env-file", default=".env",
        help="Path to .env file with SPLUNK_* credentials (default: .env)")
    parser.add_argument("--host",     help="Splunk host (overrides SPLUNK_HOST)")
    parser.add_argument("--port",     type=int, help="Splunk mgmt port (overrides SPLUNK_PORT)")
    parser.add_argument("--username", help="Splunk username (overrides SPLUNK_USERNAME)")
    parser.add_argument("--password", help="Splunk password (overrides SPLUNK_PASSWORD)")

    # --- paths ---
    parser.add_argument("--base-dir",
        help=(
            "Export base dir for JSON files "
            "(overrides SPLUNK_EXPORT_DIR; default: <script_dir>/splunk_export)"
        ))
    parser.add_argument("--meta-out-dir",
        help="Output dir for YAML files (default: <script_dir>/splunk_metadata)")
    parser.add_argument("--schema-overrides-out",
        help=(
            "Output path for the Phase 3 schema_overrides.yaml "
            "(default: <meta-out-dir>/schema_overrides.yaml). "
            "Do NOT point this at a hand-curated engine/schema_overrides.yaml "
            "unless you intend to replace its business semantics."
        ))

    # --- field stats (Phase 1, opt-in) ---
    parser.add_argument("--with-field-stats", action="store_true",
        help=(
            "Also run `| fieldsummary` per sourcetype for --field-stats-indexes "
            "and export cardinality/top-values/null-rate to field_stats.yaml. "
            "Requires a live, reachable Splunk (runs real searches)."
        ))
    parser.add_argument("--field-stats-indexes", default="chocolate_index",
        help=(
            "Comma-separated list of indexes to profile with --with-field-stats "
            "(default: chocolate_index)"
        ))
    parser.add_argument("--field-stats-sample-secs", type=int, default=None,
        help=(
            "Optional `earliest=-Ns` window for the fieldsummary search "
            "(default: full index range)"
        ))

    # --- phase control ---
    parser.add_argument("--skip-extract", action="store_true",
        help="Skip Phase 1 (Splunk JSON export); use existing splunk_export/ directory")
    parser.add_argument("--skip-gen-yaml", action="store_true",
        help="Skip Phase 2 (YAML generation from splunk_export/)")
    parser.add_argument("--skip-schema-overrides", action="store_true",
        help="Skip Phase 3 (schema_overrides.yaml synthesis from splunk_metadata/)")
    parser.add_argument("--overwrite-schema-overrides", action="store_true",
        help=(
            "Replace the schema_overrides.yaml target outright instead of merging "
            "into it. By default Phase 3 refreshes the structural layer (indexes, "
            "sourcetypes, fields) while preserving any hand-curated semantics "
            "(roles, relationships, derived_metrics, glossary, investigation "
            "patterns) already in the target file."
        ))

    return parser.parse_args()


# =========================================================
# SHARED HELPERS
# =========================================================

def as_bool(v):
    return v in ("1", 1, True, "true", "True")


def sanitize_filename(name, max_length=120):
    if not name:
        return "unknown"
    clean_name = re.sub(r'[<>:"/\\\\|?*]', '_', name)
    clean_name = (
        clean_name
        .replace("\n", "_")
        .replace("\r", "_")
        .replace("\t", "_")
    )
    if len(clean_name) > max_length:
        hash_suffix = hashlib.md5(clean_name.encode("utf-8")).hexdigest()[:10]
        clean_name = clean_name[:max_length] + "_" + hash_suffix
    return clean_name


def parse_json_str(s):
    """Parse a JSON string, returning None on any failure (falsy, non-string, or decode error)."""
    if not s or not isinstance(s, str):
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def clean(s):
    return re.sub(r"\s+", " ", s).strip() if isinstance(s, str) else s


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# =========================================================
# PHASE 1 — FILE I/O HELPERS
# =========================================================

def save_json(folder, filename, data):
    safe_filename = sanitize_filename(filename)
    filepath = os.path.join(BASE_DIR, folder, f"{safe_filename}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_error(folder, endpoint, error_msg):
    error_data = {"endpoint": endpoint, "error": str(error_msg)}
    filepath = os.path.join(BASE_DIR, "errors", f"{sanitize_filename(folder)}_error.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(error_data, f, indent=2, ensure_ascii=False)


# =========================================================
# PHASE 1 — LOOKUP FILE ENRICHMENT
# =========================================================

def classify_lookup(columns, filename):
    columns_lower = [c.lower() for c in columns] if columns else []
    filename_lower = filename.lower()

    if (
        "country" in columns_lower
        or "city" in columns_lower
        or "latitude" in columns_lower
        or "longitude" in columns_lower
    ):
        return "geoip"

    if (
        "ioc" in filename_lower
        or "threat" in filename_lower
        or "malware" in filename_lower
        or "hash" in columns_lower
        or "indicator" in columns_lower
    ):
        return "threat_intelligence"

    if (
        "user" in columns_lower
        or "username" in columns_lower
        or "department" in columns_lower
        or "email" in columns_lower
    ):
        return "identity"

    if (
        "hostname" in columns_lower
        or "asset" in columns_lower
        or "criticality" in columns_lower
        or "owner" in columns_lower
    ):
        return "asset_inventory"

    return "generic"


def _enrich_lookup_via_rest(entry):
    """Fetch columns/sample rows/row count for a CSV lookup via `| inputlookup`.

    Filesystem access to `eai:data` only works when this script runs on the
    Splunk server itself (the path is a server-local path like
    `C:\\Program Files\\Splunk\\etc\\apps\\...\\lookups\\foo.csv`). Running it
    from any other machine — the common case — always reports "file not
    found" even though Splunk can read the lookup fine. `| inputlookup` goes
    through Splunk's own lookup resolution, so it works identically whether
    we're local or remote.
    """
    name = entry.get("name", "")

    if not name.lower().endswith(".csv"):
        return False

    lookup_name = name[:-len(".csv")]

    try:
        sample_job = service.jobs.oneshot(
            f'| inputlookup "{lookup_name}" | head 5',
            output_mode="json",
        )
        sample_rows = [dict(r) for r in results.JSONResultsReader(sample_job)
                       if isinstance(r, dict)]
        columns = list(sample_rows[0].keys()) if sample_rows else []

        count_job = service.jobs.oneshot(
            f'| inputlookup "{lookup_name}" | stats count',
            output_mode="json",
        )
        count_rows = [dict(r) for r in results.JSONResultsReader(count_job)
                      if isinstance(r, dict)]
        row_count = int(count_rows[0]["count"]) if count_rows else 0

        entry["lookup_columns"] = columns
        entry["lookup_sample_rows"] = sample_rows
        entry["lookup_row_count"] = row_count
        entry["lookup_classification"] = classify_lookup(columns, entry.get("name", ""))
        entry.pop("lookup_read_error", None)
        return True

    except Exception as e:
        entry["lookup_read_error"] = f"REST fallback failed: {e}"
        return False


def enrich_lookup_file(entry):
    try:
        lookup_path = entry.get("eai:data")

        if not lookup_path:
            entry["lookup_read_error"] = "No lookup file path found"
            return entry

        if not os.path.exists(lookup_path):
            if _enrich_lookup_via_rest(entry):
                return entry
            entry["lookup_read_error"] = "Lookup file not found (and REST fallback unavailable)"
            return entry

        columns = []
        sample_rows = []
        row_count = 0

        with open(lookup_path, "r", encoding="utf-8", errors="ignore") as csvfile:
            reader = csv.DictReader(csvfile)
            columns = reader.fieldnames or []
            for row in reader:
                row_count += 1
                if len(sample_rows) < 5:
                    sample_rows.append(row)

        entry["lookup_columns"] = columns
        entry["lookup_sample_rows"] = sample_rows
        entry["lookup_row_count"] = row_count
        entry["lookup_classification"] = classify_lookup(columns, entry.get("name", ""))

    except Exception as e:
        entry["lookup_read_error"] = str(e)

    return entry


# =========================================================
# PHASE 1 — KV STORE ENRICHMENT
# =========================================================
#
# KV-store config entries carry the declared schema in `field.<name>` keys
# (when defined) but they do NOT expose the actual collection contents. To
# mirror what we do for lookup_files (columns + sample rows + row count),
# we hit the data endpoint:
#
#     /servicesNS/<owner>/<app>/storage/collections/data/<collection>
#
# - `?limit=5&output_mode=json`         → sample rows
# - `?fields=_key&limit=10000&output_mode=json` → row count (capped)
#
# For schemaless collections we synthesise columns from the union of keys
# present in the sample rows.
# =========================================================

KVSTORE_SAMPLE_LIMIT = 5
KVSTORE_COUNT_CAP = 10000


def _kvstore_acl(entry):
    """Return (owner, app) for the data endpoint. eai:acl is a nested dict."""
    acl = entry.get("eai:acl") or {}
    owner = acl.get("owner") or "nobody"
    app = acl.get("app") or entry.get("eai:appName") or "search"
    return owner, app


def _kvstore_get_json(path, **params):
    """GET a splunklib path and decode the JSON body."""
    response = service.get(path, **params)
    body = response.body.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="ignore")
    return json.loads(body)


def enrich_kvstore(entry):
    try:
        collection = entry.get("name")

        if not collection:
            entry["kvstore_read_error"] = "No collection name"
            return entry

        declared_schema = {}
        for k, v in list(entry.items()):
            if k.startswith("field."):
                declared_schema[k[len("field."):]] = v

        accelerated_fields = {}
        for k, v in list(entry.items()):
            if k.startswith("accelerated_fields."):
                accelerated_fields[k[len("accelerated_fields."):]] = v

        owner, app = _kvstore_acl(entry)
        data_path = (
            f"/servicesNS/{owner}/{app}/storage/collections/data/{collection}"
        )

        # -------------------------------------------------
        # DISABLED-COLLECTION SHORT-CIRCUIT
        #
        # Splunk responds with HTTP 400 "Collection 'X' cannot be used as it
        # is disabled." for both the sample and count calls when the
        # collection is disabled. Skip the data calls so we don't spam
        # `kvstore_read_error` for what is a deliberate config state — the
        # `disabled` flag on the config entry already carries this signal.
        # -------------------------------------------------
        if str(entry.get("disabled")) in ("1", "true", "True"):
            entry["kvstore_data_skipped"] = "collection disabled"
            entry["kvstore_columns"] = list(declared_schema.keys())
            entry["kvstore_schema"] = declared_schema or None
            entry["kvstore_accelerated_fields"] = accelerated_fields or None
            entry["kvstore_sample_rows"] = []
            entry["kvstore_row_count"] = None
            entry["kvstore_row_count_capped"] = False
            entry["kvstore_schemaless"] = not bool(declared_schema)
            return entry

        sample_rows = _kvstore_get_json(
            data_path,
            output_mode="json",
            limit=KVSTORE_SAMPLE_LIMIT,
        )
        if not isinstance(sample_rows, list):
            sample_rows = []

        if declared_schema:
            columns = list(declared_schema.keys())
        else:
            seen = []
            for row in sample_rows:
                if not isinstance(row, dict):
                    continue
                for k in row.keys():
                    if k in ("_key", "_user"):
                        continue
                    if k not in seen:
                        seen.append(k)
            columns = seen

        row_count = None
        row_count_capped = False
        try:
            keys_only = _kvstore_get_json(
                data_path,
                output_mode="json",
                fields="_key",
                limit=KVSTORE_COUNT_CAP,
            )
            if isinstance(keys_only, list):
                row_count = len(keys_only)
                if row_count == KVSTORE_COUNT_CAP:
                    row_count_capped = True
        except Exception as count_err:
            entry["kvstore_count_error"] = str(count_err)

        entry["kvstore_columns"] = columns
        entry["kvstore_schema"] = declared_schema or None
        entry["kvstore_accelerated_fields"] = accelerated_fields or None
        entry["kvstore_sample_rows"] = sample_rows
        entry["kvstore_row_count"] = row_count
        entry["kvstore_row_count_capped"] = row_count_capped
        entry["kvstore_schemaless"] = not bool(declared_schema)

    except Exception as e:
        err_msg = str(e)
        if "is disabled" in err_msg:
            entry["kvstore_data_skipped"] = "collection disabled"
        else:
            entry["kvstore_read_error"] = err_msg

    return entry


# =========================================================
# PHASE 1 — FETCH + EXPORT SPLUNK REST ENDPOINTS
# =========================================================

def fetch_entries(endpoint):
    response = service.get(endpoint, count=0)
    xml_data = response.body.read().decode("utf-8")
    root = ET.fromstring(xml_data)
    namespace = {
        'ns': 'http://www.w3.org/2005/Atom',
        's': 'http://dev.splunk.com/ns/rest',
    }
    entries = []
    for entry in root.findall('ns:entry', namespace):
        item = {}

        title = entry.find('ns:title', namespace)
        if title is not None:
            item['name'] = title.text

        id_node = entry.find('ns:id', namespace)
        if id_node is not None:
            item['id'] = id_node.text

        updated = entry.find('ns:updated', namespace)
        if updated is not None:
            item['updated'] = updated.text

        content = entry.find('ns:content', namespace)
        if content is not None:
            dict_node = content.find('s:dict', namespace)
            if dict_node is not None:
                for key in dict_node.findall('s:key', namespace):
                    key_name = key.attrib.get('name')

                    if key.text:
                        item[key_name] = key.text

                    nested_dict = key.find('s:dict', namespace)
                    if nested_dict is not None:
                        nested_data = {}
                        for nested_key in nested_dict.findall('s:key', namespace):
                            nested_name = nested_key.attrib.get('name')
                            nested_data[nested_name] = nested_key.text
                        item[key_name] = nested_data

                    nested_list = key.find('s:list', namespace)
                    if nested_list is not None:
                        values = []
                        for val in nested_list.findall('s:item', namespace):
                            values.append(val.text)
                        item[key_name] = values

        entries.append(item)
    return entries


def export_endpoint(endpoint, folder, name_field='name'):
    print(f"\nExporting {folder} ...")
    entries = fetch_entries(endpoint)
    metadata = []

    for entry in entries:
        name = entry.get(name_field, "unknown")
        safe_name = sanitize_filename(name)

        if folder == "lookup_files":
            entry = enrich_lookup_file(entry)

        if folder == "kvstore":
            entry = enrich_kvstore(entry)

        save_json(folder, safe_name, entry)
        metadata.append({"name": name, "file": f"{safe_name}.json"})

    save_json("metadata", f"{folder}_metadata", metadata)
    print(f"Exported {len(entries)} items")


EXPORTS = [
    ("/servicesNS/-/-/saved/searches",             "saved_searches"),
    ("/servicesNS/-/-/data/ui/views",               "dashboards"),
    ("/services/data/indexes",                      "indexes"),
    ("/services/configs/conf-props",                "props"),
    ("/services/configs/conf-transforms",           "transforms"),
    ("/services/data/transforms/lookups",           "lookups"),
    ("/services/data/lookup-table-files",           "lookup_files"),
    ("/servicesNS/-/-/admin/macros",                "macros"),
    ("/servicesNS/-/-/saved/eventtypes",            "eventtypes"),
    ("/servicesNS/-/-/configs/conf-tags",           "tags"),
    ("/servicesNS/-/-/datamodel/model",             "datamodels"),
    ("/services/apps/local",                        "apps"),
    ("/services/authentication/users",              "users"),
    ("/services/authorization/roles",               "roles"),
    ("/services/data/inputs/all",                   "inputs"),
    ("/services/configs/conf-fieldaliases",         "fieldaliases"),
    ("/services/configs/conf-calcfields",           "calcfields"),
    ("/servicesNS/-/-/storage/collections/config",  "kvstore"),
]


# =========================================================
# PHASE 1 — FIELD-CARDINALITY STATS (opt-in, --with-field-stats)
#
# Per-kind YAMLs tell the agent a field EXISTS but not whether it's a good
# `stats ... by` dimension (low cardinality: store_id, category) or a terrible
# one (high cardinality: order_id, customer_id on a 1.1M-row index — that's
# an OOM/slow-search footgun). `| fieldsummary` gives exactly that signal:
# distinct-value counts, null rates, and top values. This requires running
# real searches against a live, reachable Splunk — unlike every other export
# in this script (which reads pre-existing config objects), so it's opt-in
# and writes straight to splunk_metadata/ rather than round-tripping through
# splunk_export/ + Phase 2.
# =========================================================

FIELD_STATS_TOP_VALUES = 5
FIELD_STATS_SAMPLE_CAP = 200000  # cap rows scanned per sourcetype (perf guard)


def _oneshot_json(spl, **kwargs):
    job = service.jobs.oneshot(spl, output_mode="json", **kwargs)
    return [dict(r) for r in results.JSONResultsReader(job) if isinstance(r, dict)]


def _discover_sourcetypes(index_name):
    rows = _oneshot_json(
        f'| metadata type=sourcetypes index="{index_name}" | table sourcetype'
    )
    return [r["sourcetype"] for r in rows if r.get("sourcetype")]


def _profile_sourcetype(index_name, sourcetype, earliest):
    spl = (
        f'search index="{index_name}" sourcetype="{sourcetype}" {earliest} '
        f'| head {FIELD_STATS_SAMPLE_CAP} '
        f'| fieldsummary '
        f'| table field count distinct_count is_exact numeric_count max min mean stdev values'
    )
    fields = []
    for row in _oneshot_json(spl):
        values = parse_json_str(row.get("values"))
        top_values = []
        if isinstance(values, list):
            for v in values[:FIELD_STATS_TOP_VALUES]:
                if isinstance(v, dict) and "value" in v:
                    top_values.append({"value": v.get("value"), "count": v.get("count")})
        fields.append({
            "field":          row.get("field"),
            "count":          _to_int(row.get("count")),
            "distinct_count": _to_int(row.get("distinct_count")),
            "is_exact":       as_bool(row.get("is_exact")),
            "numeric_count":  _to_int(row.get("numeric_count")),
            "max":            row.get("max"),
            "min":            row.get("min"),
            "mean":           row.get("mean"),
            "stdev":          row.get("stdev"),
            "top_values":     top_values,
        })
    return fields


def _cardinality_class(field, total_count):
    """Cheap heuristic the agent can use directly: is `stats ... by <field>` safe?"""
    distinct = field.get("distinct_count")
    if distinct is None or not total_count:
        return "unknown"
    ratio = distinct / total_count
    if distinct <= 50:
        return "low (safe group-by / filter dimension)"
    if ratio < 0.01:
        return "medium (usable group-by; consider top-N limiting)"
    return "high (near-unique — do NOT `stats ... by` this; it's an identifier, not a dimension)"


def export_field_stats(indexes, earliest_secs=None):
    print(f"\nProfiling field cardinality for indexes: {', '.join(indexes)} ...")
    earliest_clause = f"earliest=-{earliest_secs}s" if earliest_secs else ""
    profiled = []

    for index_name in indexes:
        try:
            sourcetypes = _discover_sourcetypes(index_name)
        except Exception as e:
            print(f"  FAILED to discover sourcetypes for {index_name}: {e}")
            save_error("field_stats", index_name, str(e))
            continue
        for sourcetype in sourcetypes:
            try:
                fields = _profile_sourcetype(index_name, sourcetype, earliest_clause)
            except Exception as e:
                print(f"  FAILED to profile {index_name}/{sourcetype}: {e}")
                save_error("field_stats", f"{index_name}.{sourcetype}", str(e))
                continue
            total = max((f["count"] or 0) for f in fields) if fields else 0
            for f in fields:
                f["cardinality"] = _cardinality_class(f, total)
            profiled.append({
                "index":          index_name,
                "sourcetype":     sourcetype,
                "sampled_events": total,
                "sample_capped":  total >= FIELD_STATS_SAMPLE_CAP,
                "fields":         fields,
            })
            print(f"  profiled {index_name}/{sourcetype}: {len(fields)} fields, ~{total} events sampled")

    os.makedirs(META_OUT_DIR, exist_ok=True)
    out_path = os.path.join(META_OUT_DIR, "field_stats.yaml")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            yaml.dump({
                "version": 1,
                "kind": "field_stats",
                "source": "live `| fieldsummary` searches (--with-field-stats)",
                "generated_by": "splunk_pipeline.py --with-field-stats",
                "description": (
                    "Per-field cardinality/null-rate/top-values profile, sampled live "
                    "via `| fieldsummary`. `cardinality` is a direct safety signal for "
                    "the SPL generator: low-cardinality fields are safe `stats ... by` "
                    "dimensions; high-cardinality fields are identifiers that will "
                    "blow up aggregations on large indexes."
                ),
                "sample_cap_per_sourcetype": FIELD_STATS_SAMPLE_CAP,
                "profiles": profiled,
            }, fh, sort_keys=False, allow_unicode=True, width=120)
        print(f"WROTE {out_path}  ({len(profiled)} sourcetype profiles)")
    except Exception as e:
        print(f"FAILED to write {out_path}: {e}")
        save_error("field_stats", "write_yaml", str(e))


# =========================================================
# PHASE 1 — ORCHESTRATOR
# =========================================================

def run_extraction(args):
    """Connect to Splunk and export all config objects to BASE_DIR as JSON."""
    global service, client, results

    from dotenv import load_dotenv
    import splunklib.client as client
    import splunklib.results as results

    load_dotenv(args.env_file)

    splunk_host     = args.host     or os.environ.get("SPLUNK_HOST", "localhost")
    splunk_port     = args.port     or int(os.environ.get("SPLUNK_PORT", "8089"))
    splunk_username = args.username or os.environ.get("SPLUNK_USERNAME")
    splunk_password = args.password or os.environ.get("SPLUNK_PASSWORD")

    if not splunk_username or not splunk_password:
        raise SystemExit(
            "Missing Splunk credentials. Set SPLUNK_USERNAME/SPLUNK_PASSWORD in "
            f"{args.env_file} or pass --username/--password."
        )

    service = client.connect(
        host=splunk_host,
        port=splunk_port,
        username=splunk_username,
        password=splunk_password,
    )

    folders = [
        "dashboards", "saved_searches", "indexes", "props", "transforms",
        "lookups", "lookup_files", "macros", "eventtypes", "tags", "datamodels",
        "apps", "users", "roles", "inputs", "fieldaliases", "calcfields",
        "kvstore", "metadata", "errors",
    ]
    for folder in folders:
        os.makedirs(os.path.join(BASE_DIR, folder), exist_ok=True)

    for endpoint, folder in EXPORTS:
        try:
            export_endpoint(endpoint, folder)
        except Exception as e:
            print(f"FAILED: {folder}")
            print(str(e))
            save_error(folder, endpoint, str(e))

    if args.with_field_stats:
        export_field_stats(
            [s.strip() for s in args.field_stats_indexes.split(",") if s.strip()],
            earliest_secs=args.field_stats_sample_secs,
        )

    save_json("metadata", "export_summary", {
        "total_export_types": len(EXPORTS),
        "export_base_dir": BASE_DIR,
    })

    print("\n===================================")
    print("DONE — FULL SPLUNK EXPORT COMPLETE")
    print("===================================")
    print(f"Export Location: {BASE_DIR}")


# =========================================================
# PHASE 2 — YAML HELPERS
# =========================================================

def literal_repr(dumper, data):
    if "\n" in data or len(data) > 100:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def dump_yaml(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, sort_keys=False, allow_unicode=True, width=120)


# =========================================================
# PHASE 2 — DASHBOARD GENERATION
# =========================================================

VIZ_TAGS = {"chart", "table", "single", "event", "map", "html", "viz"}


def _parse_v1(xml_str):
    """v1 XML: <dashboard><row><panel><title><chart|table|...><search><query>..."""
    panels = []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        try:
            root = ET.fromstring(f"<root>{xml_str}</root>")
        except ET.ParseError:
            return panels
    for panel in root.iter("panel"):
        title_el = panel.find("title")
        title = clean(title_el.text) if title_el is not None and title_el.text else None
        for child in panel:
            if child.tag not in VIZ_TAGS:
                continue
            child_title = clean(child.findtext("title")) or title
            search = child.find("search")
            if search is None:
                continue
            q = clean(search.findtext("query"))
            if not q:
                continue
            entry = {"title": child_title or "(untitled)", "viz": child.tag, "query": q}
            for f in ("earliest", "latest"):
                v = clean(search.findtext(f))
                if v:
                    entry[f] = v
            ref = search.attrib.get("ref")
            if ref:
                entry["saved_search_ref"] = ref
            panels.append(entry)
    return panels


def _parse_v2(xml_str):
    """v2: <definition><![CDATA[ {JSON: dataSources + visualizations + layout} ]]></definition>"""
    m = re.search(r"<definition>\s*<!\[CDATA\[(.*?)\]\]>\s*</definition>", xml_str, re.DOTALL)
    if not m:
        return []
    defn = parse_json_str(m.group(1))
    if not defn:
        return []
    dss  = defn.get("dataSources", {})
    vizs = defn.get("visualizations", {})
    panels, used = [], set()
    for v in vizs.values():
        title = clean(v.get("title")) or clean(v.get("name")) or "(untitled)"
        vtype = v.get("type", "viz")
        ds_ref = (v.get("dataSources") or {}).get("primary")
        if not ds_ref:
            continue
        ds = dss.get(ds_ref)
        if not ds or ds.get("type") != "ds.search":
            continue
        used.add(ds_ref)
        q = clean((ds.get("options") or {}).get("query"))
        if not q:
            continue
        entry = {"title": title, "viz": vtype, "query": q}
        opts = ds.get("options") or {}
        qp   = opts.get("queryParameters") or {}
        if qp.get("earliest"): entry["earliest"] = qp["earliest"]
        if qp.get("latest"):   entry["latest"]   = qp["latest"]
        panels.append(entry)
    for dk, ds in dss.items():
        if dk in used or ds.get("type") != "ds.search":
            continue
        q = clean((ds.get("options") or {}).get("query"))
        if not q:
            continue
        panels.append({
            "title": clean(ds.get("name")) or f"(input feeder {dk})",
            "viz": "input_feeder",
            "query": q,
        })
    return panels


def _classify_dash(eai_data):
    if not eai_data:
        return "empty", []
    if "<query>" in eai_data:
        return "v1", _parse_v1(eai_data)
    if "<definition>" in eai_data and "dataSources" in eai_data:
        return "v2", _parse_v2(eai_data)
    return "view", []


def gen_dashboards():
    os.makedirs(META_OUT_DIR, exist_ok=True)
    dash_dir = os.path.join(SRC, "dashboards")
    out   = []
    stats = {"v1": 0, "v2": 0, "view": 0, "empty": 0, "skipped_example": 0, "total": 0}
    for fn in sorted(os.listdir(dash_dir)):
        if not fn.endswith(".json"):
            continue
        if fn.startswith("example-"):
            stats["skipped_example"] += 1
            continue
        stats["total"] += 1
        with open(os.path.join(dash_dir, fn), encoding="utf-8") as fh:
            d = json.load(fh)
        kind, panels = _classify_dash(d.get("eai:data", ""))
        stats[kind] += 1
        if kind in ("view", "empty") or not panels:
            continue
        out.append({
            "name":        d.get("label") or d.get("name"),
            "id":          d.get("name"),
            "app":         d.get("eai:appName"),
            "version":     d.get("version"),
            "format":      kind,
            "disabled":    as_bool(d.get("disabled")),
            "panel_count": len(panels),
            "panels":      panels,
        })
    dump_yaml(DASH_OUT, {
        "version":      1,
        "source":       "splunk_export/dashboards",
        "generated_by": "splunk_pipeline.py",
        "stats":        stats,
        "dashboards":   out,
    })
    print(f"WROTE {DASH_OUT}  ({len(out)} dashboards, {sum(x['panel_count'] for x in out)} panels)")


# =========================================================
# PHASE 2 — METADATA EXTRACTORS
# =========================================================

def app_appname_first(d):
    """Default app resolution: eai:appName → fall back to eai:acl.app."""
    a = d.get("eai:appName")
    if a:
        return a
    return (d.get("eai:acl") or {}).get("app")


def app_acl_first(d):
    """Override resolution: eai:acl.app → fall back to eai:appName.

    Lookups use this — eai:appName ('search') is the consumer app, but eai:acl.app
    ('system') is where the lookup definition actually lives.
    """
    return (d.get("eai:acl") or {}).get("app") or d.get("eai:appName")


def x_datamodels(d):
    accel = parse_json_str(d.get("acceleration")) or {}
    desc  = parse_json_str(d.get("description"))  or {}
    objects = []
    for o in (desc.get("objects") or []):
        obj_search  = clean(o.get("objectSearch"))
        constraints = [clean(c.get("search")) for c in (o.get("constraints") or []) if c.get("search")]
        objects.append({
            "object_name":  o.get("objectName"),
            "display_name": o.get("displayName"),
            "parent_name":  o.get("parentName"),
            "lineage":      o.get("lineage"),
            "search":       obj_search,
            "constraints":  constraints,
        })
    return {
        "name":                d.get("name"),
        "display_name":        d.get("displayName"),
        "app":                 app_appname_first(d),
        "disabled":            as_bool(d.get("disabled")),
        "dataset_type":        d.get("dataset.type"),
        "acceleration_allowed": as_bool(d.get("acceleration.allowed")),
        "acceleration": {
            "enabled":         accel.get("enabled"),
            "cron_schedule":   accel.get("cron_schedule"),
            "earliest_time":   accel.get("earliest_time"),
            "max_time":        accel.get("max_time"),
            "backfill_time":   accel.get("backfill_time"),
            "manual_rebuilds": accel.get("manual_rebuilds"),
        } if accel else None,
        "description":    desc.get("description")    if isinstance(desc, dict) else None,
        "object_summary": desc.get("objectSummary")  if isinstance(desc, dict) else None,
        "tags_whitelist": (d.get("tags_whitelist") or "").split(",") if d.get("tags_whitelist") else [],
        "objects": objects,
    }


def x_eventtypes(d):
    return {
        "name":     d.get("name"),
        "app":      app_appname_first(d),
        "disabled": as_bool(d.get("disabled")),
        "priority": d.get("priority"),
        "color":    d.get("color"),
        "search":   clean(d.get("search")),
        "tags":     d.get("tags") or [],
    }


def x_indexes(d):
    return {
        "name":                   d.get("name"),
        "app":                    app_appname_first(d),
        "disabled":               as_bool(d.get("disabled")),
        "datatype":               d.get("datatype"),
        "home_path":              d.get("homePath"),
        "cold_path":              d.get("coldPath"),
        "thawed_path":            d.get("thawedPath"),
        "max_data_size":          d.get("maxDataSize"),
        "frozen_time_period_secs": d.get("frozenTimePeriodInSecs"),
        "total_event_count":      d.get("totalEventCount"),
        "current_db_size_mb":     d.get("currentDBSizeMB"),
        "min_time":               d.get("minTime"),
        "max_time":               d.get("maxTime"),
    }


def x_inputs(d):
    return {
        "name":       d.get("name"),
        "app":        app_appname_first(d),
        "disabled":   as_bool(d.get("disabled")),
        "type":       d.get("eai:type"),
        "location":   d.get("eai:location"),
        "index":      d.get("index"),
        "sourcetype": d.get("sourcetype"),
        "source":     d.get("source"),
        "host":       d.get("host"),
        "interval":   d.get("interval"),
        "queue":      d.get("queue"),
    }


def x_kvstore(d):
    """KV-store collection.

    From a Splunk-agent lens the high-value fields are:
      - columns + schema    so the agent can map field names to types when
                            writing `| inputlookup <collection>` queries
      - sample_rows         concrete shape for prompt grounding (cap 3 like
                            lookup_files)
      - row_count + capped  size hint (capped at 10k by the exporter)
      - schemaless          flips agent to "fields inferred from data" mode
      - accelerated_fields  index hints — agent should prefer these in WHERE
      - replicate           SHC visibility flag
    """
    rows  = d.get("kvstore_sample_rows") or []
    accel = d.get("kvstore_accelerated_fields") or {}
    return {
        "name":                    d.get("name"),
        "app":                     app_appname_first(d),
        "disabled":                as_bool(d.get("disabled")),
        "type":                    d.get("type"),
        "replicate":               as_bool(d.get("replicate")),
        "replication_dump_strategy": d.get("replication_dump_strategy"),
        "profiling_enabled":       as_bool(d.get("profilingEnabled")),
        "schemaless":              d.get("kvstore_schemaless"),
        "columns":                 d.get("kvstore_columns") or [],
        "schema":                  d.get("kvstore_schema") or {},
        "accelerated_fields":      accel,
        "row_count":               d.get("kvstore_row_count"),
        "row_count_capped":        d.get("kvstore_row_count_capped") or None,
        "sample_rows":             rows[:3],
        "read_error":              d.get("kvstore_read_error"),
        "data_skipped":            d.get("kvstore_data_skipped"),
    }


def x_lookup_files(d):
    rows = d.get("lookup_sample_rows") or []
    return {
        "name":           d.get("name"),
        "app":            app_appname_first(d),
        "disabled":       as_bool(d.get("disabled")),
        "file_path":      d.get("eai:data"),
        "row_count":      d.get("lookup_row_count"),
        "classification": d.get("lookup_classification"),
        "columns":        d.get("lookup_columns") or [],
        "sample_rows":    rows[:3],
    }


def x_lookups(d):
    return {
        "name":                 d.get("name"),
        "app":                  app_acl_first(d),
        "disabled":             as_bool(d.get("disabled")),
        "type":                 d.get("type"),
        "external_cmd":         d.get("external_cmd"),
        "fields":               d.get("fields_array") or [],
        "source_key":           d.get("SOURCE_KEY"),
        "match_limit":          d.get("MATCH_LIMIT"),
        "case_sensitive_match": d.get("case_sensitive_match"),
    }


def x_macros(d):
    args = d.get("args")
    if isinstance(args, str):
        args = [a.strip() for a in args.split(",") if a.strip()]
    return {
        "name":       d.get("name"),
        "app":        app_appname_first(d),
        "disabled":   as_bool(d.get("disabled")),
        "iseval":     as_bool(d.get("iseval")),
        "args":       args or [],
        "definition": clean(d.get("definition")),
    }


def x_saved_searches(d):
    raw     = d.get("actions")
    actions = [a.strip() for a in raw.split(",") if a.strip()] if isinstance(raw, str) else (raw or [])
    return {
        "name":                  d.get("name"),
        "app":                   app_appname_first(d),
        "disabled":              as_bool(d.get("disabled")),
        "is_scheduled":          as_bool(d.get("is_scheduled")),
        "cron_schedule":         d.get("cron_schedule"),
        "dispatch_earliest_time": d.get("dispatch.earliest_time"),
        "dispatch_latest_time":  d.get("dispatch.latest_time"),
        "alert_type":            d.get("alert_type"),
        "alert_severity":        d.get("alert.severity"),
        "actions":               actions,
        "description":           clean(d.get("description")),
        "search":                clean(d.get("qualifiedSearch") or d.get("search")),
    }


_PROPS_BOILERPLATE = {
    "ADD_EXTRA_TIME_FIELDS", "ANNOTATE_PUNCT", "BREAK_ONLY_BEFORE_DATE",
    "DATETIME_CONFIG", "DEPTH_LIMIT", "DETERMINE_TIMESTAMP_DATE_WITH_SYSTEM_TIME",
    "LB_CHUNK_BREAKER_TRUNCATE", "LEARN_MODEL", "LEARN_SOURCETYPE",
    "LINE_BREAKER_LOOKBEHIND", "MATCH_LIMIT", "MAX_DAYS_AGO", "MAX_DAYS_HENCE",
    "MAX_DIFF_SECS_AGO", "MAX_DIFF_SECS_HENCE", "MAX_EVENTS",
    "MAX_EXPECTED_EVENT_LINES", "MAX_TIMESTAMP_LOOKAHEAD", "NO_BINARY_CHECK",
    "detect_trailing_nulls", "maxDist", "termFrequencyWeightedDist",
    "trackPipelineLatency", "unarchive_cmd", "unarchive_cmd_start_mode",
    "id", "updated", "eai:userName",
}


def _group_stanzas(d, prefix):
    """Collect keys like 'EXTRACT-foo' → {'foo': value}. Prefix excludes trailing '-'."""
    out = {}
    for k, v in d.items():
        if k.startswith(prefix + "-"):
            out[k[len(prefix) + 1:]] = clean(v) if isinstance(v, str) else v
    return out


def _non_default(v, default):
    """Return v iff it differs from `default` (allowing multiple default sentinels)."""
    defaults = default if isinstance(default, (tuple, set, list)) else (default,)
    return v if v is not None and v not in defaults else None


def x_props(d):
    name = d.get("name", "")
    if name.startswith("source::"):
        target_kind = "source"
    elif name.startswith("host::"):
        target_kind = "host"
    else:
        target_kind = "sourcetype"
    routing_raw = d.get("TRANSFORMS")
    routing = [s.strip() for s in routing_raw.split(",")] if isinstance(routing_raw, str) else []
    return {
        "name":             name,
        "target_kind":      target_kind,
        "app":              app_appname_first(d),
        "disabled":         as_bool(d.get("disabled")),
        "category":         d.get("category"),
        "description":      clean(d.get("description")),
        "pulldown_type":    d.get("pulldown_type"),
        "line_breaker":     d.get("LINE_BREAKER"),
        "should_linemerge": d.get("SHOULD_LINEMERGE"),
        "must_break_after": d.get("MUST_BREAK_AFTER"),
        "break_only_before": d.get("BREAK_ONLY_BEFORE"),
        "event_breaker":    d.get("EVENT_BREAKER"),
        "event_breaker_enable": as_bool(d.get("EVENT_BREAKER_ENABLE")) if d.get("EVENT_BREAKER_ENABLE") is not None else None,
        "truncate":         _non_default(d.get("TRUNCATE"), "10000"),
        "time_prefix":      d.get("TIME_PREFIX"),
        "time_format":      d.get("TIME_FORMAT"),
        "tz":               d.get("TZ"),
        "max_timestamp_lookahead": _non_default(d.get("MAX_TIMESTAMP_LOOKAHEAD"), "128"),
        "indexed_extractions":     d.get("INDEXED_EXTRACTIONS"),
        "header_field_delimiter":  d.get("HEADER_FIELD_DELIMITER"),
        "field_header_regex":      d.get("FIELD_HEADER_REGEX"),
        "kv_mode":          d.get("KV_MODE"),
        "auto_kv_json":     as_bool(d.get("AUTO_KV_JSON")),
        "charset":          _non_default(d.get("CHARSET"), "UTF-8"),
        "extractions":      _group_stanzas(d, "EXTRACT"),
        "reports":          _group_stanzas(d, "REPORT"),
        "field_aliases":    _group_stanzas(d, "FIELDALIAS"),
        "evals":            _group_stanzas(d, "EVAL"),
        "lookups":          _group_stanzas(d, "LOOKUP"),
        "transforms_chain": _group_stanzas(d, "TRANSFORMS"),
        "sed_cmds":         _group_stanzas(d, "SEDCMD"),
        "index_time_routing": routing,
    }


def x_transforms(d):
    if d.get("external_cmd") or d.get("external_type"):
        kind = "external"
    elif d.get("collection"):
        kind = "kvstore_lookup"
    elif d.get("filename"):
        kind = "file_lookup"
    elif d.get("REGEX"):
        kind = "regex"
    elif d.get("DELIMS"):
        kind = "delimited"
    else:
        kind = "other"
    fields_list = d.get("fields_list")
    if isinstance(fields_list, str):
        fields_list = [f.strip() for f in fields_list.split(",") if f.strip()]
    dest_key = d.get("DEST_KEY")
    routes_to_nullqueue = isinstance(d.get("FORMAT"), str) and "nullQueue" in d.get("FORMAT", "")
    return {
        "name":            d.get("name"),
        "app":             app_appname_first(d),
        "disabled":        as_bool(d.get("disabled")),
        "kind":            kind,
        "regex":           d.get("REGEX"),
        "format":          d.get("FORMAT"),
        "delims":          d.get("DELIMS"),
        "fields":          d.get("FIELDS") or fields_list or [],
        "source_key":      _non_default(d.get("SOURCE_KEY"), "_raw"),
        "dest_key":        dest_key,
        "drops_events":    routes_to_nullqueue or None,
        "external_type":   d.get("external_type"),
        "external_cmd":    d.get("external_cmd"),
        "python_required": d.get("python.required"),
        "filename":        d.get("filename"),
        "collection":      d.get("collection"),
        "mv_add":          _non_default(d.get("MV_ADD"), "0"),
        "clean_keys":      _non_default(d.get("CLEAN_KEYS"), "1"),
        "write_meta":      _non_default(d.get("WRITE_META"), ("0", False)),
        "can_optimize":    _non_default(d.get("CAN_OPTIMIZE"), "1"),
    }


def x_tags(d):
    from urllib.parse import unquote
    name_raw = d.get("name") or ""
    target_kind, _, target_val = name_raw.partition("=")
    try:
        target_val = unquote(target_val)
    except Exception:
        pass
    STATIC   = {"name", "id", "updated", "disabled", "eai:acl", "eai:appName", "eai:userName"}
    enabled  = [k for k, v in d.items() if k not in STATIC and v == "enabled"]
    disabled = [k for k, v in d.items() if k not in STATIC and v == "disabled"]
    return {
        "name":          name_raw,
        "app":           app_appname_first(d),
        "disabled":      as_bool(d.get("disabled")),
        "target_kind":   target_kind,
        "target":        target_val,
        "tags_enabled":  enabled,
        "tags_disabled": disabled,
    }


EXTRACTORS = OrderedDict([
    ("datamodels",    x_datamodels),
    ("eventtypes",    x_eventtypes),
    ("indexes",       x_indexes),
    ("inputs",        x_inputs),
    ("kvstore",       x_kvstore),
    ("lookup_files",  x_lookup_files),
    ("lookups",       x_lookups),
    ("macros",        x_macros),
    ("props",         x_props),
    ("saved_searches", x_saved_searches),
    ("tags",          x_tags),
    ("transforms",    x_transforms),
])


# =========================================================
# PHASE 2 — SPL PATTERN MINING
# =========================================================
# Saved searches and macros encode how *this* Splunk instance's senior
# authors actually write SPL — real join/lookup/stats/tstats idioms beat an
# agent inventing syntax from general training knowledge. We mine the already
# -extracted `search`/`definition` strings (no live Splunk needed) for
# command-level snippets, grouped by command, deduplicated, and capped — a
# concrete "house style" reference the SPL generator can be shown alongside
# the schema.

SPL_IDIOM_COMMANDS = (
    "stats", "tstats", "join", "append", "appendcols", "lookup", "inputlookup",
    "outputlookup", "eval", "where", "rex", "spath", "bin", "foreach",
    "mvexpand", "mvcombine", "transpose", "makejson", "tojson", "rename",
)
IDIOM_EXAMPLES_PER_COMMAND = 6
IDIOM_SNIPPET_MAX_LEN = 220

_IDIOM_RE = re.compile(
    r"\|\s*(" + "|".join(SPL_IDIOM_COMMANDS) + r")\b([^|]*)",
    re.IGNORECASE,
)


def _extract_idioms(spl):
    if not spl or not isinstance(spl, str):
        return []
    found = []
    for m in _IDIOM_RE.finditer(spl):
        command = m.group(1).lower()
        snippet = clean(f"| {command}{m.group(2)}")
        if len(snippet) > IDIOM_SNIPPET_MAX_LEN:
            snippet = snippet[:IDIOM_SNIPPET_MAX_LEN].rstrip() + " ..."
        found.append((command, snippet))
    return found


def gen_spl_patterns():
    """Mine saved_searches + macros for real SPL idioms, grouped by command."""
    os.makedirs(META_OUT_DIR, exist_ok=True)
    sources = []
    for kind, key in (("saved_searches", "search"), ("macros", "definition")):
        path = os.path.join(META_OUT_DIR, f"{kind}.yaml")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for item in doc.get("items", []):
            spl = item.get(key)
            if spl:
                sources.append({"origin": kind, "name": item.get("name"), "spl": spl})

    by_command = OrderedDict((c, OrderedDict()) for c in SPL_IDIOM_COMMANDS)
    for src in sources:
        for command, snippet in _extract_idioms(src["spl"]):
            bucket = by_command[command]
            if snippet not in bucket:
                bucket[snippet] = src["name"]

    patterns = []
    for command, examples in by_command.items():
        if not examples:
            continue
        patterns.append({
            "command":    command,
            "occurrences": len(examples),
            "examples": [
                {"snippet": snippet, "from": origin}
                for snippet, origin in list(examples.items())[:IDIOM_EXAMPLES_PER_COMMAND]
            ],
        })

    out_path = os.path.join(META_OUT_DIR, "spl_patterns.yaml")
    dump_yaml(out_path, {
        "version":      1,
        "kind":         "spl_patterns",
        "source":       "splunk_metadata/saved_searches.yaml + macros.yaml (mined offline)",
        "generated_by": "splunk_pipeline.py",
        "description": (
            "Real SPL idioms mined from this Splunk instance's saved searches "
            "and macros, grouped by command. Shows the agent HOW commands are "
            "actually shaped here (argument order, common flag combos, join "
            "patterns) rather than relying on generic training knowledge."
        ),
        "scanned": {
            "saved_searches": sum(1 for s in sources if s["origin"] == "saved_searches"),
            "macros":         sum(1 for s in sources if s["origin"] == "macros"),
        },
        "commands": patterns,
    })
    print(f"WROTE {out_path}  ({len(patterns)} commands, "
          f"{sum(p['occurrences'] for p in patterns)} distinct idioms)")


# =========================================================
# PHASE 2 — KNOWLEDGE-OBJECT DEPENDENCY GRAPH
# =========================================================

_MACRO_REF_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)(?:\([^)]*\))?`")
_DATAMODEL_REF_RE = re.compile(
    r"(?:\|\s*datamodel\s+|from\s+datamodel\s*=\s*)([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
_SAVEDSEARCH_REF_RE = re.compile(r"\|\s*savedsearch\s+[\"']?([^\"'|]+)", re.IGNORECASE)


def _spl_references(spl, known_macros, known_searches, known_datamodels):
    """Return {kind: set(names)} of known knowledge objects referenced in `spl`."""
    refs = {"macro": set(), "saved_search": set(), "datamodel": set()}
    if not spl or not isinstance(spl, str):
        return refs
    for name in _MACRO_REF_RE.findall(spl):
        if name in known_macros:
            refs["macro"].add(name)
    for name in _DATAMODEL_REF_RE.findall(spl):
        if name in known_datamodels:
            refs["datamodel"].add(name)
    for raw in _SAVEDSEARCH_REF_RE.findall(spl):
        name = raw.strip().strip('"')
        if name in known_searches:
            refs["saved_search"].add(name)
    return refs


def _load_kind_yaml(kind):
    path = os.path.join(META_OUT_DIR, f"{kind}.yaml")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def gen_dependencies():
    """Build a dashboard -> {saved_search, macro, datamodel} dependency edge list."""
    os.makedirs(META_OUT_DIR, exist_ok=True)
    dash_doc       = yaml.safe_load(open(DASH_OUT, encoding="utf-8")) if os.path.exists(DASH_OUT) else {}
    macros_doc     = _load_kind_yaml("macros")       or {"items": []}
    searches_doc   = _load_kind_yaml("saved_searches") or {"items": []}
    datamodels_doc = _load_kind_yaml("datamodels")   or {"items": []}

    known_macros     = {it["name"] for it in macros_doc.get("items", [])     if it.get("name")}
    known_searches   = {it["name"] for it in searches_doc.get("items", [])   if it.get("name")}
    known_datamodels = {it["name"] for it in datamodels_doc.get("items", []) if it.get("name")}

    edges      = []
    dash_stats = {"dashboards_scanned": 0, "dashboards_with_deps": 0}
    for dash in dash_doc.get("dashboards", []):
        dash_stats["dashboards_scanned"] += 1
        refs = {"macro": set(), "saved_search": set(), "datamodel": set()}
        for panel in dash.get("panels", []):
            ref = panel.get("saved_search_ref")
            if ref and ref in known_searches:
                refs["saved_search"].add(ref)
            for kind, names in _spl_references(
                panel.get("query"), known_macros, known_searches, known_datamodels
            ).items():
                refs[kind] |= names
        if any(refs.values()):
            dash_stats["dashboards_with_deps"] += 1
            edges.append({
                "dashboard":  dash.get("id") or dash.get("name"),
                "depends_on": {k: sorted(v) for k, v in refs.items() if v},
            })

    object_edges = []
    for kind, doc, key in (
        ("saved_search", searches_doc, "search"),
        ("macro",        macros_doc,   "definition"),
    ):
        for item in doc.get("items", []):
            refs = _spl_references(item.get(key), known_macros, known_searches, known_datamodels)
            refs[kind].discard(item.get("name"))
            if any(refs.values()):
                object_edges.append({
                    "object_kind": kind,
                    "object_name": item.get("name"),
                    "depends_on":  {k: sorted(v) for k, v in refs.items() if v},
                })

    out_path = os.path.join(META_OUT_DIR, "dependencies.yaml")
    dump_yaml(out_path, {
        "version":      1,
        "kind":         "dependencies",
        "source":       "splunk_metadata/{dashboards,saved_searches,macros,datamodels}.yaml (cross-referenced offline)",
        "generated_by": "splunk_pipeline.py",
        "description": (
            "Knowledge-object dependency graph recovered by cross-referencing "
            "dashboard panel queries, saved searches, and macros against known "
            "object names (saved_search_ref, `macro` calls, | savedsearch, "
            "| datamodel / from datamodel=...). Answers 'what does X depend on' "
            "/ 'what breaks if I change Y' impact-analysis questions that are "
            "invisible in the flat per-kind exports."
        ),
        "stats":                  dash_stats,
        "dashboard_dependencies": edges,
        "object_dependencies":    object_edges,
    })
    print(f"WROTE {out_path}  ({len(edges)} dashboards with deps, "
          f"{len(object_edges)} saved-search/macro cross-refs)")


# =========================================================
# PHASE 2 — METADATA GENERATION
# =========================================================

def gen_metadata():
    os.makedirs(META_OUT_DIR, exist_ok=True)
    rows = []
    for kind, extractor in EXTRACTORS.items():
        src_dir = os.path.join(SRC, kind)
        if not os.path.isdir(src_dir):
            print(f"SKIP {kind}: dir missing")
            continue
        items = []
        for fn in sorted(os.listdir(src_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(src_dir, fn), encoding="utf-8") as fh:
                try:
                    d = json.load(fh)
                except json.JSONDecodeError:
                    continue
            entry = extractor(d)
            entry = {k: v for k, v in entry.items() if v not in (None, "", [], {})}
            items.append(entry)
        out_path = os.path.join(META_OUT_DIR, f"{kind}.yaml")
        dump_yaml(out_path, {
            "version":      1,
            "kind":         kind,
            "source":       f"splunk_export/{kind}",
            "generated_by": "splunk_pipeline.py",
            "count":        len(items),
            "items":        items,
        })
        rows.append((kind, len(items), out_path))
        print(f"WROTE {out_path}  ({len(items)} items)")
    print("\nSUMMARY")
    for k, n, p in rows:
        print(f"  {k:18s} {n:5d}  {p}")


# =========================================================
# PHASE 2 — ORCHESTRATOR
# =========================================================

def run_yaml_gen():
    """Read splunk_export/ JSON files and generate splunk_metadata/ YAMLs."""
    yaml.add_representer(str, literal_repr)
    gen_dashboards()
    print()
    gen_metadata()
    print()
    gen_spl_patterns()
    gen_dependencies()


# =========================================================
# PHASE 3 — SCHEMA OVERRIDES SYNTHESIS
# =========================================================
# Turns the flat per-kind Phase 2 YAMLs into the single schema_overrides.yaml
# the SOC agent consumes (SchemaManager). The agent's SPL accuracy hinges on
# four things this file supplies:
#
#   1. KNOWN INDEXES        — the guardrail rejects any `index=` it doesn't
#                             recognise; an authoritative list prevents the
#                             generator from inventing index names.
#   2. INDEX -> SOURCETYPES  — which sourcetypes actually live in each index.
#                             `inputs.yaml` is unreliable (index is usually the
#                             literal "default"), so we recover real pairings
#                             from `index=X sourcetype=Y` co-occurrences inside
#                             eventtype / saved-search / datamodel / macro SPL,
#                             restricted to indexes that genuinely exist.
#   3. FIELDS (+ cardinality) — from field_stats.yaml when --with-field-stats
#                             was run (authoritative + a safe-`stats by` signal);
#                             otherwise the calculated (EVAL-) and REPORT-extracted
#                             field names recoverable from props/transforms.
#   4. BUILDING BLOCKS      — pre-built eventtype filters (e.g. CIM
#                             cim:authentication), enrichment lookups/kvstore
#                             collections, accelerated datamodels (tstats
#                             targets) and macros. These let the agent REUSE the
#                             instance's own knowledge objects instead of
#                             hand-rolling brittle equivalents.
#
# Everything here is derived mechanically from config — no dataset-specific
# assumptions — so it bootstraps a usable schema for any standalone Splunk.
# What it deliberately does NOT invent is business semantics (fact/dimension
# roles, KPI formulas, a domain glossary); those stay the job of a curated
# overrides file layered on top.
# =========================================================

_INDEX_RE       = re.compile(r"\bindex\s*=\s*\"?([A-Za-z0-9_*-]+)\"?")
_SOURCETYPE_RE  = re.compile(r"\bsourcetype\s*=\s*\"?([A-Za-z0-9_:*.\-]+)\"?")
SCHEMA_DESC_MAX = 600  # keep rendered descriptions lean (consumer truncates at 12k chars)


def _load_meta(name):
    """Load a Phase 2 metadata YAML (<meta>/<name>.yaml); return {} if absent/unreadable."""
    path = os.path.join(META_OUT_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError:
        return {}


def _meta_items(name):
    """Return the `items` list of a per-kind metadata YAML (empty list if absent)."""
    return _load_meta(name).get("items", []) or []


def _strip_wildcard(s):
    return s[:-1] if isinstance(s, str) and s.endswith("*") else s


def _index_role(name, datatype):
    """Heuristic role for the planner — purely structural, no business meaning."""
    if datatype == "metric":
        return "metrics"
    low = name.lower()
    if low == "_audit":
        return "audit"
    if low.startswith("_"):
        return "operational"
    return "data"


def _discover_index_sourcetypes(known_indexes):
    """Recover real {index: {sourcetypes}} pairings from config.

    `inputs.yaml` carries explicit index+sourcetype but the index is almost
    always the literal "default", so it's only a weak signal. The strong signal
    is `index=X sourcetype=Y` co-occurrence inside the SPL the instance already
    runs — eventtypes, saved searches, datamodel constraints/searches, macros.
    We only keep indexes that actually exist (known_indexes) so malformed or
    wildcard `index=` tokens can't pollute the map.
    """
    mapping = defaultdict(set)

    for it in _meta_items("inputs"):
        idx = (it.get("index") or "").strip()
        st = (it.get("sourcetype") or "").strip()
        if idx in known_indexes and st:
            mapping[idx].add(_strip_wildcard(st))

    spl_blobs = []
    spl_blobs += [it.get("search") for it in _meta_items("eventtypes")]
    spl_blobs += [it.get("search") for it in _meta_items("saved_searches")]
    spl_blobs += [it.get("definition") for it in _meta_items("macros")]
    for dm in _meta_items("datamodels"):
        for obj in dm.get("objects", []):
            spl_blobs += obj.get("constraints", []) or []
            spl_blobs.append(obj.get("search"))

    for spl in spl_blobs:
        if not isinstance(spl, str):
            continue
        indexes = [i for i in _INDEX_RE.findall(spl) if "*" not in i and i in known_indexes]
        sourcetypes = [_strip_wildcard(s) for s in _SOURCETYPE_RE.findall(spl) if "*" not in s.rstrip("*")]
        for idx in indexes:
            for st in sourcetypes:
                mapping[idx].add(st)

    return mapping


def _field_stats_index():
    """Index field_stats.yaml profiles by (index, sourcetype) -> fields list."""
    out = {}
    for profile in _load_meta("field_stats").get("profiles", []):
        idx = profile.get("index")
        st = _strip_wildcard(profile.get("sourcetype") or "")
        if not idx or not st:
            continue
        fields = []
        for f in profile.get("fields", []):
            name = f.get("field")
            if not name or name.startswith("_"):
                continue
            descr = f.get("cardinality")  # already a "safe to stats-by?" sentence
            entry = {"name": name}
            if descr:
                entry["description"] = descr
            fields.append(entry)
        out[(idx, st)] = fields
    return out


def _props_by_sourcetype():
    """Map sourcetype -> its props entry (only sourcetype-targeting stanzas)."""
    out = {}
    for p in _meta_items("props"):
        if p.get("target_kind") == "sourcetype" and p.get("name"):
            out[p["name"]] = p
    return out


def _transforms_by_name():
    out = {}
    for t in _meta_items("transforms"):
        if t.get("name"):
            out[t["name"]] = t
    return out


def _derive_fields_from_props(st, props_by_st, transforms_by_name):
    """Best-effort field NAMES when there is no live field_stats profile.

    Only fields we can name with confidence: EVAL-<field> calculated fields
    (the stanza suffix IS the field name) and the FIELDS list of any transform a
    REPORT- stanza references. Regex EXTRACT-/FIELDALIAS- internals are left out
    deliberately — guessing them produces invalid field names, which is worse
    for the agent than an honest gap.
    """
    props = props_by_st.get(st) or props_by_st.get(_strip_wildcard(st))
    if not props:
        return []
    names = []
    for fname in (props.get("evals") or {}):
        names.append(fname)
    for transform_ref in (props.get("reports") or {}).values():
        for ref in str(transform_ref).split(","):
            tr = transforms_by_name.get(ref.strip())
            if tr:
                for f in tr.get("fields", []):
                    if f and f not in names:
                        names.append(f)
    return [{"name": n} for n in names]


def _sourcetype_description(st, props_by_st):
    """Factual parsing-mode hint so the agent knows how fields are discovered."""
    props = props_by_st.get(st) or props_by_st.get(_strip_wildcard(st))
    if not props:
        return None
    bits = []
    if props.get("description"):
        bits.append(clean(props["description"]))
    ix = props.get("indexed_extractions")
    if ix:
        bits.append(f"Structured {ix} input — fields auto-extracted from the data's own keys (dynamic).")
    if props.get("kv_mode") and props.get("kv_mode") not in ("none",):
        bits.append(f"KV_MODE={props['kv_mode']} — key=value fields auto-extracted at search time (dynamic).")
    text = " ".join(bits).strip()
    return text[:SCHEMA_DESC_MAX] or None


def _build_glossary():
    """Capability map: business intent -> the knowledge object that answers it.

    Generic because it is sourced entirely from the instance's own eventtypes
    (pre-built filters, e.g. CIM cim:authentication) and classified lookups
    (enrichment tables) — not from any assumed domain vocabulary.
    """
    glossary = []
    for et in _meta_items("eventtypes"):
        search = et.get("search")
        if et.get("disabled") or not et.get("name") or not search:
            continue
        # Only eventtypes that pin an index are actionable pre-built filters;
        # skip pure string-match noise (e.g. internal_search_terms' huge OR blob).
        if "index=" not in search:
            continue
        tags = ", ".join(et.get("tags", []) or [])
        term = f"{et['name']}" + (f" ({tags})" if tags else "")
        glossary.append({
            "term": term,
            "maps_to": f"eventtype `{et['name']}`: {clean(search)[:160]}",
        })
    for lf in _meta_items("lookup_files"):
        cls = lf.get("classification")
        if cls and cls != "generic" and lf.get("name"):
            cols = ", ".join(lf.get("columns", []) or [])
            glossary.append({
                "term": f"{cls} enrichment",
                "maps_to": f"lookup `{lf['name']}` (columns: {cols})",
            })
    return glossary


def _build_investigation_patterns():
    """Generic, factual patterns recovered from accelerated datamodels.

    Each accelerated CIM-style datamodel is a high-performance `tstats` target;
    naming it (with its real constraints) is concrete, instance-specific
    guidance the agent can apply without us fabricating analytic steps.
    """
    patterns = []
    for dm in _meta_items("datamodels"):
        if dm.get("disabled") or not dm.get("name"):
            continue
        accel = dm.get("acceleration") or {}
        # Summarise SCOPE (which index/sourcetype the model covers) rather than
        # dumping every object's raw constraint — the full definitions are huge
        # and low-value to the agent, and would crowd out the consumer's context
        # budget. The scope is the actionable "is this model relevant here" hint.
        constraints = []
        for obj in dm.get("objects", []):
            constraints += obj.get("constraints", []) or []
        scope_idx = sorted({i for c in constraints if isinstance(c, str)
                            for i in _INDEX_RE.findall(c) if "*" not in i})
        scope_st = sorted({_strip_wildcard(s) for c in constraints if isinstance(c, str)
                          for s in _SOURCETYPE_RE.findall(c)})
        steps = [
            f"Use `| tstats <agg> from datamodel={dm['name']} by <field>` for fast, "
            f"index-time aggregation instead of a raw search when the question fits this model.",
        ]
        if scope_idx or scope_st:
            scope = []
            if scope_idx:
                scope.append("indexes: " + ", ".join(scope_idx))
            if scope_st:
                scope.append("sourcetypes: " + ", ".join(scope_st))
            steps.append("Scope — " + "; ".join(scope))
        steps.append(
            f"Acceleration {'ENABLED' if accel.get('enabled') else 'NOT enabled'} — "
            f"if not accelerated, tstats falls back to a normal search (slower)."
        )
        patterns.append({
            "name": f"datamodel_{dm['name']}",
            "applies_to": dm.get("description") or dm.get("display_name") or dm["name"],
            "steps": steps,
        })
    return patterns


# Splunk-internal indexes a SOC analyst realistically queries. Generic — these
# names exist on every standalone Splunk. All other `_*` indexes are platform
# plumbing and are omitted (unless the instance has CIM/datamodel knowledge
# objects built on them) so they don't crowd the agent's context.
USEFUL_INTERNAL_INDEXES = {"_audit", "_internal", "_introspection"}

# Splunk platform apps that are operational infrastructure, not SOC analytics.
# Their macros, saved searches, and lookups are noise for the agent.
_PLATFORM_INTERNAL_APPS = frozenset({
    "SplunkDeploymentServerConfig",
    "splunk_monitoring_console",
    "splunk_instrumentation",
    "splunk_secure_gateway",
    "python_upgrade_readiness_app",
})


def _indexes_with_knowledge_objects(known_indexes):
    """Internal indexes that have CIM eventtypes / datamodels built on them.

    Presence of a knowledge object is a strong signal the index is analytically
    meaningful (someone curated searches for it), so it's worth keeping even if
    it isn't in the canonical USEFUL_INTERNAL_INDEXES set.
    """
    found = set()
    blobs = [et.get("search") for et in _meta_items("eventtypes")]
    for dm in _meta_items("datamodels"):
        for obj in dm.get("objects", []):
            blobs += obj.get("constraints", []) or []
            blobs.append(obj.get("search"))
    for spl in blobs:
        if isinstance(spl, str):
            for i in _INDEX_RE.findall(spl):
                if "*" not in i and i in known_indexes:
                    found.add(i)
    return found


def _include_index(name, it, ko_indexes):
    """Keep only indexes the agent can realistically and usefully query.

    Business indexes: include if they actually hold data. Internal indexes:
    only the canonical analyst-relevant ones, or ones carrying CIM/datamodel
    knowledge objects — everything else is Splunk plumbing (noise).
    """
    if name.startswith("_"):
        return name in USEFUL_INTERNAL_INDEXES or name in ko_indexes
    return str(it.get("total_event_count") or "") != "0"


def _index_description(it, role):
    """Minimal, high-signal index description (no timestamps / filler)."""
    if it.get("datatype") == "metric":
        return "Metrics index — query with `| mstats` (metric data, not events)."
    if (it.get("name") or "").startswith("_"):
        return f"Splunk-internal index ({role})."
    return None  # business index: structure + any curated description carry the meaning


_SCHEMA_OVERRIDES_HEADER = (
    "Auto-generated by splunk_pipeline.py (Phase 3) from splunk_metadata/. "
    "Generic structural schema (indexes/sourcetypes/fields) for a standalone "
    "Splunk; merge-preserves any curated business semantics. "
    "Re-run with --with-field-stats for per-field cardinality."
)


def _union_fields(generated, curated):
    """Union two field lists by name; curated entries win (they carry semantics)."""
    def fname(f):
        return f.get("name") if isinstance(f, dict) else f
    merged = {fname(f): f for f in generated}
    for f in curated:
        merged[fname(f)] = f
    return list(merged.values())


def _merge_overrides(generated, curated):
    """Overlay a curated schema_overrides doc on top of the freshly generated one.

    The generated doc supplies the up-to-date STRUCTURE (which indexes,
    sourcetypes and fields currently exist in the user's Splunk); the curated
    doc supplies SEMANTICS a config scan cannot infer (business roles,
    relationships, derived_metrics, descriptions, glossary, investigation
    patterns). On any conflict the curated value wins, so re-running the
    pipeline against the same Splunk refreshes the structure without discarding
    human curation. New indexes/sourcetypes/fields present only in the generated
    doc are added; ones present only in the curated doc are preserved.

    NOTE: this round-trips through YAML, so comments in the curated file are not
    preserved — only its data.
    """
    merged = dict(generated)

    g_idx = generated.get("indexes", {}) or {}
    c_idx = curated.get("indexes", {}) or {}
    out_idx = {}
    for name in list(g_idx) + [n for n in c_idx if n not in g_idx]:
        g = g_idx.get(name, {}) or {}
        c = c_idx.get(name, {}) or {}
        entry = {**g, **{k: v for k, v in c.items() if k != "sourcetypes"}}  # curated scalars win

        g_st = g.get("sourcetypes", {}) or {}
        c_st = c.get("sourcetypes", {}) or {}
        out_st = {}
        for st in list(g_st) + [s for s in c_st if s not in g_st]:
            gs = g_st.get(st, {}) or {}
            cs = c_st.get(st, {}) or {}
            st_entry = {**gs, **{k: v for k, v in cs.items() if k != "fields"}}
            fields = _union_fields(gs.get("fields", []) or [], cs.get("fields", []) or [])
            if fields:
                st_entry["fields"] = fields
            out_st[st] = st_entry
        if out_st:
            entry["sourcetypes"] = out_st
        out_idx[name] = entry
    merged["indexes"] = out_idx

    # Curated-authored, free-text knowledge: keep the human version wholesale
    # when present (do not dilute it with auto-derived entries).
    for key in ("glossary", "investigation_patterns"):
        if curated.get(key):
            merged[key] = curated[key]

    # Preserve any other curated top-level keys the generator doesn't emit.
    for k, v in curated.items():
        if k not in merged:
            merged[k] = v

    return merged


def _build_eventtypes():
    """Pre-built SPL filters — agent uses `eventtype=<name>` instead of hand-coding filters."""
    out = []
    for et in _meta_items("eventtypes"):
        if et.get("disabled") or not et.get("name") or not et.get("search"):
            continue
        search = (et["search"] or "").strip()
        # Skip catchall noise eventtypes (internal_search_terms = giant OR blob)
        if len(search) > 400:
            continue
        entry: dict = {"name": et["name"], "search": clean(search)[:200]}
        tags = [t for t in (et.get("tags") or []) if t]
        if tags:
            entry["tags"] = tags
        out.append(entry)
    return out


def _build_macros():
    """SPL macros the agent can call as `name` or `name(arg)` inline in queries.

    Only macros that actually query Splunk data (definition references index= or
    aliases another macro). Utility-only macros (string helpers, REST admin
    commands, statistical histogram utilities) are excluded — they don't help
    the agent write better analytics or security SPL.
    """
    out = []
    for m in _meta_items("macros"):
        if m.get("disabled") or m.get("app") in _PLATFORM_INTERNAL_APPS:
            continue
        name = m.get("name") or ""
        defn = (m.get("definition") or "").strip()
        if not name or not defn:
            continue
        if m.get("iseval") and not any(kw in defn for kw in ("|", "if(", "case(", "match(")):
            continue
        # Keep only macros that query data or alias macros that do
        if "index=" not in defn and not defn.startswith("`"):
            continue
        args = m.get("args") or []
        base_name = re.sub(r'\(\d+\)$', '', name)
        call = f"`{base_name}`" if not args else f"`{base_name}({', '.join(args)})`"
        out.append({"call": call, "definition": clean(defn)[:200]})
    return out


def _build_lookups():
    """Lookup tables available for `| lookup <name> <key_field>` in SPL."""
    # Map well-known lookup names to usage hints so the agent knows when to apply them
    _LOOKUP_HINTS: dict = {
        "dnslookup": "resolve IP→hostname or hostname→IP; key fields: clientip or clienthost",
        "geo_attr_countries": "enrich country name with region/continent/ISO codes; key field: country",
        "geo_attr_us_states": "enrich US state abbreviation with full name and FIPS code; key field: state_code",
        "geo_countries": "geospatial country boundaries for map visualizations; key field: country",
        "geo_us_states": "geospatial US state boundaries for map visualizations; key field: state_name",
    }
    out = []
    seen: set = set()
    for lk in _meta_items("lookups"):
        if lk.get("disabled") or lk.get("app") in _PLATFORM_INTERNAL_APPS:
            continue
        name = lk.get("name") or ""
        if not name or name in seen:
            continue
        fields = [f for f in (lk.get("fields") or []) if f not in ("_key", "_raw")]
        if not fields:
            continue
        seen.add(name)
        entry: dict = {"name": name, "type": lk.get("type") or "csv", "fields": fields[:8]}
        if hint := _LOOKUP_HINTS.get(name):
            entry["use_when"] = hint
        out.append(entry)
    return out


def _build_soc_queries():
    """Scheduled saved searches — proven SPL patterns active in this environment."""
    out = []
    for ss in _meta_items("saved_searches"):
        if ss.get("disabled") or not ss.get("is_scheduled"):
            continue
        if ss.get("app") in _PLATFORM_INTERNAL_APPS:
            continue
        name = ss.get("name") or ""
        search = (ss.get("search") or "").strip()
        if not name or not search:
            continue
        entry: dict = {"name": name, "query": clean(search)[:300]}
        if ss.get("description"):
            entry["description"] = ss["description"]
        out.append(entry)
    return out[:25]


def gen_schema_overrides(overwrite=False):
    """Synthesise schema_overrides.yaml from the Phase 2 metadata YAMLs.

    By default merges into an existing target (refresh structure, keep curated
    semantics). Pass overwrite=True for a clean, fully generated file.
    """
    yaml.add_representer(str, literal_repr)

    index_items = _meta_items("indexes")
    known_indexes = {it["name"] for it in index_items if it.get("name")}
    if not known_indexes:
        print("SKIP schema_overrides: no indexes.yaml — run Phase 2 first.")
        return

    idx_st_map        = _discover_index_sourcetypes(known_indexes)
    field_stats       = _field_stats_index()
    props_by_st       = _props_by_sourcetype()
    transforms_by_name = _transforms_by_name()

    ko_indexes = _indexes_with_knowledge_objects(known_indexes)

    # Business (non-internal) indexes first, then internal `_*` ones, alpha
    # within each group. The consumer truncates the rendered schema at a fixed
    # char budget, so the highest-value indexes must render first.
    indexes_out = {}
    for it in sorted(index_items, key=lambda x: (x.get("name", "").startswith("_"), x.get("name", ""))):
        name = it.get("name")
        if not name or not _include_index(name, it, ko_indexes):
            continue
        datatype = it.get("datatype")
        role = _index_role(name, datatype)

        sourcetypes_out = {}
        for st in sorted(idx_st_map.get(name, [])):
            fields = field_stats.get((name, st))
            if not fields:
                fields = _derive_fields_from_props(st, props_by_st, transforms_by_name)
            st_descr = _sourcetype_description(st, props_by_st)
            # Skip bare sourcetypes we know nothing concrete about: a name with
            # no fields and no parsing hint gives the agent no SPL-writing signal
            # (role is just the index role, time_field is always _time) — it's
            # pure context weight. Curated sourcetypes are merged in separately.
            if not fields and not st_descr:
                continue
            st_entry: dict = {"role": role, "time_field": "_time"}
            if st_descr:
                st_entry["description"] = st_descr
            if fields:
                st_entry["fields"] = fields
            sourcetypes_out[st] = st_entry

        index_entry: dict = {"role": role}
        descr = _index_description(it, role)
        if descr:
            index_entry["description"] = descr
        if sourcetypes_out:
            index_entry["sourcetypes"] = sourcetypes_out
        indexes_out[name] = index_entry

    doc: dict = {}
    doc["_generated_note"] = _SCHEMA_OVERRIDES_HEADER
    doc["indexes"] = indexes_out
    glossary = _build_glossary()
    if glossary:
        doc["glossary"] = glossary
    patterns = _build_investigation_patterns()
    if patterns:
        doc["investigation_patterns"] = patterns
    eventtypes = _build_eventtypes()
    if eventtypes:
        doc["eventtypes"] = eventtypes
    macros = _build_macros()
    if macros:
        doc["macros"] = macros
    lookups = _build_lookups()
    if lookups:
        doc["lookups"] = lookups
    soc_queries = _build_soc_queries()
    if soc_queries:
        doc["soc_queries"] = soc_queries

    merged_note = ""
    if not overwrite and os.path.exists(SCHEMA_OVERRIDES_OUT):
        try:
            with open(SCHEMA_OVERRIDES_OUT, encoding="utf-8") as fh:
                curated = yaml.safe_load(fh) or {}
            if curated:
                doc = _merge_overrides(doc, curated)
                merged_note = " (merged into existing — curated semantics preserved)"
        except yaml.YAMLError:
            print(f"  WARN: could not parse existing {SCHEMA_OVERRIDES_OUT}; overwriting.")

    os.makedirs(os.path.dirname(SCHEMA_OVERRIDES_OUT or ".") or ".", exist_ok=True)
    dump_yaml(SCHEMA_OVERRIDES_OUT, doc)

    st_count = sum(len(i.get("sourcetypes", {})) for i in doc.get("indexes", {}).values())
    print(
        f"WROTE {SCHEMA_OVERRIDES_OUT}{merged_note}  "
        f"({len(doc.get('indexes', {}))} indexes, {st_count} sourcetypes, "
        f"{len(doc.get('eventtypes', []))} eventtypes, "
        f"{len(doc.get('macros', []))} macros, "
        f"{len(doc.get('lookups', []))} lookups, "
        f"{len(doc.get('soc_queries', []))} soc_queries)"
    )
    if not field_stats:
        print("  NOTE: no field_stats.yaml — per-field cardinality omitted. "
              "Re-run with --with-field-stats for full field profiles.")


# =========================================================
# MAIN
# =========================================================

def main():
    global BASE_DIR, SRC, META_OUT_DIR, DASH_OUT, SCHEMA_OVERRIDES_OUT

    args = parse_args()

    BASE_DIR     = args.base_dir     or os.environ.get("SPLUNK_EXPORT_DIR",
                        os.path.join(ROOT_DIR, "splunk_export"))
    META_OUT_DIR = args.meta_out_dir or os.path.join(ROOT_DIR, "splunk_metadata")
    SRC          = BASE_DIR
    DASH_OUT     = os.path.join(META_OUT_DIR, "dashboards.yaml")
    # Default the overrides output to the path the SOC agent actually loads
    # (engine/schema_overrides.yaml, relative to the engine cwd). Fall back to
    # the metadata dir when this isn't the engine-bearing repo layout.
    engine_overrides = os.path.join(ROOT_DIR, "engine", "schema_overrides.yaml")
    default_overrides = (
        engine_overrides if os.path.isdir(os.path.join(ROOT_DIR, "engine"))
        else os.path.join(META_OUT_DIR, "schema_overrides.yaml")
    )
    SCHEMA_OVERRIDES_OUT = args.schema_overrides_out or default_overrides

    if not args.skip_extract:
        run_extraction(args)

    if not args.skip_gen_yaml:
        run_yaml_gen()

    if not args.skip_schema_overrides:
        print()
        gen_schema_overrides(overwrite=args.overwrite_schema_overrides)


if __name__ == "__main__":
    main()
