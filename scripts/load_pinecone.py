"""Create DeepWhiz's Pinecone index and load embedded JSONL chunks (Python 3.10+).

No third-party dependencies. Dry-run by default; --apply enables remote writes.
This script consumes embeddings; it does not parse HTML or generate embeddings.
"""
import argparse
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "2025-10"
EXPERIMENT = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
TICKERS = set(EXPERIMENT["tickers"])


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PineconeAPI:
    def __init__(self, key):
        self.key = key
        self.opener = build_opener(NoRedirect())

    def request(self, method, url, payload=None, allowed=()):
        body = encode(payload) if payload is not None else None
        for attempt in range(4):
            request = Request(url, data=body, method=method, headers={
                "Api-Key": self.key, "Content-Type": "application/json",
                "X-Pinecone-Api-Version": API_VERSION,
            })
            try:
                with self.opener.open(request, timeout=60) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                status = error.code
                error.close()
                if status in allowed:
                    return status, {}
                if status not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(f"Pinecone {method}: HTTP {status}. Check permissions, quota, and configuration.") from None
            except (URLError, TimeoutError):
                # Do not replay ambiguous index creation. A rerun first describes it.
                raise RuntimeError("Pinecone network request failed. Rerun to inspect the index or safely re-upsert stable IDs.") from None
            time.sleep(2 ** attempt)
        raise RuntimeError("Pinecone request failed.")


def validate_index(index, config):
    for field in ("name", "dimension", "metric", "vector_type", "deletion_protection"):
        if index.get(field) != config[field]:
            raise ValueError(f"Existing index {field} differs; no changes made. Choose a compatible index.")
    if any(index.get("spec", {}).get("serverless", {}).get(k) != v
           for k, v in config["spec"]["serverless"].items()):
        raise ValueError("Existing index cloud/region differs; no changes made.")
    if index.get("tags", {}).get("embedding-model") != config["tags"]["embedding-model"]:
        raise ValueError("Existing index embedding-model tag differs; review its embedding provenance.")


def ensure_index(api, config, create=False, sleep=time.sleep, attempts=60):
    endpoint = "https://api.pinecone.io/indexes/" + quote(config["name"], safe="")
    status, index = api.request("GET", endpoint, allowed=(404,))
    if status == 404:
        if not create:
            raise ValueError("Index missing. Rerun with --create-index to create it.")
        api.request("POST", "https://api.pinecone.io/indexes", config, allowed=(409,))
        print(f"Requested index creation: {config['name']}")
    else:
        validate_index(index, config)
    for _ in range(attempts):
        status, index = api.request("GET", endpoint, allowed=(404,))
        if status == 200:
            validate_index(index, config)
            if index.get("status", {}).get("ready"):
                host = index.get("host", "")
                parsed = urlparse("https://" + host)
                if (not parsed.hostname or not parsed.hostname.endswith(".pinecone.io")
                        or parsed.netloc != host or parsed.path or parsed.query or parsed.fragment
                        or parsed.username or parsed.port):
                    raise ValueError("Unexpected Pinecone data-plane host.")
                return host
        sleep(5)
    raise RuntimeError("Index is still provisioning. Rerun to check readiness; no index was deleted.")


def read_records(path, config, strategy, catalog):
    """Prevalidate the complete input before any remote index creation or upsert."""
    allowed = {(c["tickers"][0], f["accession"]): f["url"]
               for c in catalog["companies"] if c["tickers"][0] in TICKERS
               for f in c["filings"]}
    records, ids = [], set()
    with path.open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or set(record) != {"id", "values", "metadata"}:
                    raise ValueError("Each record must contain exactly id, values, metadata.")
                identifier = record["id"]
                if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or not identifier.isascii():
                    raise ValueError("id must be a nonempty ASCII string of at most 512 characters.")
                if identifier in ids:
                    raise ValueError("Duplicate record ID.")
                values = record["values"]
                if not isinstance(values, list) or len(values) != config["dimension"]:
                    raise ValueError(f"Expected {config['dimension']} vector values.")
                if not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 3.4028235e38 for v in values):
                    raise ValueError("Vector values must be finite float32-compatible numbers.")
                if not any(values):
                    raise ValueError("Zero vectors are not valid embeddings for this loader.")
                meta = record["metadata"]
                if not isinstance(meta, dict):
                    raise ValueError("metadata must be an object.")
                for key, value in meta.items():
                    valid = isinstance(value, (str, bool)) or (type(value) in (int, float) and math.isfinite(value)) or (isinstance(value, list) and all(isinstance(v, str) for v in value))
                    if not key or key.startswith("$") or not valid:
                        raise ValueError("Metadata must be flat strings, finite numbers, booleans, or string lists; no nulls.")
                for key in ("ticker", "accession", "text", "source_url", "strategy", "embedding_model"):
                    if not isinstance(meta.get(key), str) or not meta[key].strip():
                        raise ValueError(f"Missing string metadata: {key}")
                if meta["strategy"] != strategy:
                    raise ValueError("Record strategy does not match --strategy.")
                if meta["embedding_model"] != config["tags"]["embedding-model"]:
                    raise ValueError("Record embedding_model does not match index configuration.")
                if allowed.get((meta["ticker"], meta["accession"])) != meta["source_url"]:
                    raise ValueError("Ticker/accession/source_url does not match the selected 16 filings.")
                if len(encode(meta)) > 39000:
                    raise ValueError("Metadata exceeds this loader's conservative 39,000-byte limit.")
                ids.add(identifier)
                records.append(record)
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError(f"Invalid JSONL line {line_number}: {error}") from None
    if not records:
        raise ValueError("Input contains no records.")
    return records


def batches(records, namespace, max_records=100, max_bytes=1800000):
    batch = []
    size = len(encode({"namespace": namespace, "vectors": []}))
    for record in records:
        added = len(encode(record)) + (1 if batch else 0)
        if batch and (len(batch) >= max_records or size + added > max_bytes):
            yield {"namespace": namespace, "vectors": batch}
            batch, size = [], len(encode({"namespace": namespace, "vectors": []}))
            added = len(encode(record))
        if size + added > max_bytes:
            raise ValueError("Single vector record exceeds the batch byte limit.")
        batch.append(record)
        size += added
    if batch:
        yield {"namespace": namespace, "vectors": batch}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSONL with precomputed chunk embeddings")
    parser.add_argument("--strategy", choices=("fixed", "semantic"))
    parser.add_argument("--run-id", default=EXPERIMENT["run_id"], help="New value for each changed corpus or chunking configuration")
    parser.add_argument("--create-index", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Create/upsert remotely; otherwise dry run")
    args = parser.parse_args()
    if not args.input and not args.create_index:
        parser.error("Supply --create-index, --input, or both.")
    if args.input and not args.strategy:
        parser.error("--input requires --strategy fixed|semantic.")
    if not re.fullmatch(r"[a-z0-9-]{1,40}", args.run_id):
        parser.error("--run-id must be 1–40 lowercase letters, digits, or hyphens.")
    config = json.loads((ROOT / "pinecone/index.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "data/filings.json").read_text(encoding="utf-8"))
    namespace = f"sec-{args.strategy}-{args.run_id}" if args.input else None
    records = read_records(args.input, config, args.strategy, catalog) if args.input else []
    payloads = list(batches(records, namespace)) if records else []
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "index": config,
                      "namespace": namespace, "records": len(records), "batches": len(payloads)}, indent=2))
    if not args.apply:
        print("Validated locally. No API key required; no network requests made.")
        return
    key = os.environ.get("PINECONE_API_KEY") or getpass.getpass("Pinecone API key: ")
    if not key.strip():
        raise ValueError("Pinecone API key is required.")
    api = PineconeAPI(key)
    host = ensure_index(api, config, args.create_index)
    uploaded = 0
    for payload in payloads:
        _, result = api.request("POST", f"https://{host}/vectors/upsert", payload)
        if result.get("upsertedCount") != len(payload["vectors"]):
            raise RuntimeError("Unexpected upsert count; rerun the same input to retry stable IDs.")
        uploaded += result["upsertedCount"]
        print(f"Acknowledged {uploaded}/{len(records)} records in {namespace}")
    print(f"Index ready: {host}. Pinecone acknowledged {uploaded} records; search visibility may lag.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
