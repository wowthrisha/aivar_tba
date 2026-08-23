#!/usr/bin/env python3
"""D-34 / Step 3A — a runnable, reproducible fuzz matrix for
POST /v1/actions/evaluate.

No "~89-case fuzz matrix" existed anywhere in this repo before this file -
D-29's own investigation only ever enumerated 4 confirmed crash shapes
(NaN, Infinity, null byte, 500-deep nesting). This script constructs a
comprehensive matrix from the actual validation boundaries enforced in
app/schemas.py (MAX_STRING_LENGTH, MAX_PARAMS_BYTES, MAX_PARAMS_DEPTH,
control-char/UTF-8-encodability rejection, affected_records ge=0 and
finite, enum validation, action_type<->reversibility consistency) plus
those 4 D-29 shapes, so it is reproducible rather than reconstructed by
hand each time.

Usage:
    python3 governance/evidence/fuzz-matrix.py <base_url> [--out results.json]

Exit code is non-zero if any case returns a 5xx (BLOCKING) or if any
case's outcome doesn't match its expected 2xx/4xx classification.

History: first run 2026-08-23 against Railway + AWS Lambda at commit
ed7095d found 3 blocking defects (nested NaN/Infinity in params, an
unpaired Unicode surrogate in a string field) - see governance/plan/
03-errors-and-fixes.md, D-34. Six cases were originally mislabeled by
construction error (an off-by-one in the nesting-depth helper, a
per-string-length cap confused with the total-payload-size cap, and
Pydantic's normal bool-as-int coercion misread as a defect) - corrected
here so the matrix is trustworthy on re-run, not just documented as wrong.
"""
import argparse
import copy
import json
import subprocess
import sys

BASE = {
    "agent_id": "fuzz-agent",
    "action_type": "update",
    "resource": "fuzz/1",
    "params": {"resource_id": 1, "fields": {"x": 1}},
    "reversibility": "update_without_snapshot",
    "affected_records": 1,
    "regulatory": "none",
}


def _mk(overrides):
    d = copy.deepcopy(BASE)
    d.update(overrides)
    return d


def _deep_params(leaf_depth):
    """Builds nested {"nested": ...} dicts so the innermost scalar leaf is
    checked at exactly `leaf_depth` by app/schemas.py::_validate_params_value
    (which checks depth at the TOP of every call, including for the leaf
    scalar itself - so `leaf_depth - 1` wrapper dicts around a base
    {"v": 0} puts the leaf's own check at `leaf_depth`)."""
    p = {"v": 0}
    for _ in range(leaf_depth - 1):
        p = {"nested": p}
    return p


CASES = []


def case(name, payload=None, raw=None, expect_4xx=True):
    CASES.append({"name": name, "payload": payload, "raw": raw, "expect_4xx": expect_4xx})


# ---- affected_records: type/range/finiteness ----
case("affected_records negative -1", _mk({"affected_records": -1}))
case("affected_records negative -5", _mk({"affected_records": -5}))
case("affected_records negative large", _mk({"affected_records": -1_000_000}))
case("affected_records NaN", raw=json.dumps(_mk({})).replace('"affected_records": 1', '"affected_records": NaN'))
case("affected_records Infinity", raw=json.dumps(_mk({})).replace('"affected_records": 1', '"affected_records": Infinity'))
case("affected_records -Infinity", raw=json.dumps(_mk({})).replace('"affected_records": 1', '"affected_records": -Infinity'))
case("affected_records string", _mk({"affected_records": "five"}))
case("affected_records null", _mk({"affected_records": None}))
case("affected_records boolean (Pydantic coerces bool->int; NOT a defect)",
     _mk({"affected_records": True}), expect_4xx=False)
case("affected_records list", _mk({"affected_records": [1]}))
case("affected_records dict", _mk({"affected_records": {"n": 1}}))
case("affected_records missing", {k: v for k, v in BASE.items() if k != "affected_records"})

# ---- params: null bytes / control chars ----
case("params null byte in value", _mk({"params": {"x": "abc\x00def"}}))
case("params null byte in key", _mk({"params": {"a\x00b": "value"}}))
case("params control char 0x01", _mk({"params": {"x": "abc\x01def"}}))
case("params control char DEL 0x7f", _mk({"params": {"x": "abc\x7fdef"}}))
case("params control char in nested value", _mk({"params": {"outer": {"inner": "bad\x02char"}}}))
case("params control char in list item", _mk({"params": {"items": ["ok", "bad\x03char"]}}))
case("params tab/newline/CR allowed (negative control)", _mk({"params": {"x": "a\tb\nc\rd"}}), expect_4xx=False)

# ---- params: nesting depth (D-34: corrected off-by-one) ----
case("params leaf at depth 20 (at limit, negative control)", _mk({"params": _deep_params(20)}), expect_4xx=False)
case("params leaf at depth 21 (one over limit)", _mk({"params": _deep_params(21)}))
case("params leaf at depth 50", _mk({"params": _deep_params(50)}))
case("params leaf at depth 100", _mk({"params": _deep_params(100)}))
case("params leaf at depth 500", _mk({"params": _deep_params(500)}))

# ---- params: total serialized size vs per-string length (D-34: corrected -
# these are two INDEPENDENT caps; a negative control for one must not
# accidentally trip the other) ----
case("params well under 64000 bytes total, each string under 10000 chars (negative control)",
     _mk({"params": {f"f{i}": "a" * 9_000 for i in range(1, 7)}}), expect_4xx=False)
case("params 64001 bytes in one string (trips MAX_STRING_LENGTH first)", _mk({"params": {"x": "a" * 64_050}}))
case("params 100KB in one string", _mk({"params": {"x": "a" * 100_000}}))
case("params 1MB in one string", _mk({"params": {"x": "a" * 1_000_000}}))

# ---- params: wrong type entirely ----
case("params is a string", _mk({"params": "not-a-dict"}))
case("params is an int", _mk({"params": 5}))
case("params is null", _mk({"params": None}))
case("params is a list", _mk({"params": [1, 2, 3]}))
case("params missing", {k: v for k, v in BASE.items() if k != "params"})

# ---- top-level string fields: null byte / control char ----
for field in ("agent_id", "action_type", "resource"):
    case(f"{field} contains null byte", _mk({field: f"bad\x00{field}"}))
    case(f"{field} contains control char 0x01", _mk({field: f"bad\x01{field}"}))
case("idempotency_key contains null byte", _mk({"idempotency_key": "bad\x00key"}))
case("idempotency_key contains control char", _mk({"idempotency_key": "bad\x01key"}))

# ---- top-level string fields: exceeds MAX_STRING_LENGTH ----
case("agent_id exceeds 10000 chars", _mk({"agent_id": "a" * 10_001}))
case("action_type exceeds 10000 chars", _mk({"action_type": "a" * 10_001}))
case("resource exceeds 10000 chars", _mk({"resource": "a" * 10_001}))
case("idempotency_key exceeds 10000 chars", _mk({"idempotency_key": "a" * 10_001}))
case("agent_id way over length (100000 chars)", _mk({"agent_id": "a" * 100_000}))

# ---- top-level string fields: wrong type ----
for field in ("agent_id", "action_type", "resource"):
    case(f"{field} is an integer", _mk({field: 5}))
    case(f"{field} is null", _mk({field: None}))
case("idempotency_key is an integer", _mk({"idempotency_key": 12345}))
case("idempotency_key is a list", _mk({"idempotency_key": ["a"]}))

# ---- reversibility / regulatory enum validation ----
case("reversibility invalid enum value", _mk({"reversibility": "not_a_real_value"}))
case("reversibility is an integer", _mk({"reversibility": 5}))
case("reversibility is null", _mk({"reversibility": None}))
case("reversibility missing", {k: v for k, v in BASE.items() if k != "reversibility"})
case("regulatory invalid enum value", _mk({"regulatory": "not_a_real_value"}))
case("regulatory is an integer", _mk({"regulatory": 5}))
case("regulatory is null", _mk({"regulatory": None}))
case("regulatory missing", {k: v for k, v in BASE.items() if k != "regulatory"})

# ---- action_type <-> reversibility consistency (Finding 4b) ----
case("action_type=read with reversibility=irreversible (mismatch)",
     _mk({"action_type": "read", "reversibility": "irreversible", "params": {}}))
case("action_type=delete with reversibility=read (mismatch)",
     _mk({"action_type": "delete", "reversibility": "read", "params": {"resource_id": 1}}))
case("action_type=send with reversibility=update_with_snapshot (mismatch)",
     _mk({"action_type": "send", "reversibility": "update_with_snapshot",
          "params": {"recipient": "x", "payload": "y"}}))
case("action_type=pay with reversibility=read (mismatch)",
     _mk({"action_type": "pay", "reversibility": "read", "params": {"amount": 1, "recipient": "x"}}))

# ---- missing required fields, one at a time ----
for field in ("agent_id", "action_type", "resource", "params", "reversibility", "affected_records", "regulatory"):
    case(f"missing required field: {field}", {k: v for k, v in BASE.items() if k != field})

# ---- malformed body shape entirely ----
case("body is a JSON array, not an object", raw="[1, 2, 3]")
case("body is a JSON string", raw='"just a string"')
case("body is a JSON number", raw="42")
case("body is null", raw="null")
case("body is empty object", raw="{}")

# ---- malformed JSON syntax ----
case("truncated JSON", raw='{"agent_id": "x", "action_type"')
case("trailing comma", raw='{"agent_id": "x",}')
case("not JSON at all", raw="this is not json")

# ---- D-29's original 4 crash shapes, nested inside params instead of
# top-level (D-34: the untyped-params-dict boundary, not affected_records) ----
case("params nested NaN value",
     raw=json.dumps(_mk({"params": {"x": 1}})).replace('{"x": 1}', '{"x": NaN}'))
case("params nested Infinity value",
     raw=json.dumps(_mk({"params": {"x": 1}})).replace('{"x": 1}', '{"x": Infinity}'))
case("params nested -Infinity value",
     raw=json.dumps(_mk({"params": {"x": 1}})).replace('{"x": 1}', '{"x": -Infinity}'))
case("resource contains unpaired unicode surrogate",
     raw=json.dumps(_mk({})).replace('"fuzz/1"', r'"bad\ud800resource"'))
case("params value contains unpaired unicode surrogate",
     raw=json.dumps(_mk({"params": {"x": "y"}})).replace('"y"', r'"bad\ud800value"'))
case("params key contains unpaired unicode surrogate",
     raw=json.dumps(_mk({"params": {"x": "y"}})).replace('"x"', r'"bad\ud800key"'))
case("params deeply nested via list-of-lists to depth 21",
     _mk({"params": {"a": [[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]}}))
case("affected_records negative float -1.5", _mk({"affected_records": -1.5}))
case("affected_records very large negative float", _mk({"affected_records": -1e300}))
case("resource contains only control characters", _mk({"resource": "\x01\x02\x03"}))
case("reversibility empty string", _mk({"reversibility": ""}))
case("params valid unicode and nested structure (negative control)",
     _mk({"params": {"note": "café résumé naïve 日本語 🎉", "nested": {"a": {"b": [1, 2, {"c": "ok"}]}}}}),
     expect_4xx=False)


def run(base_url: str, out_path: str) -> int:
    results = []
    for i, c in enumerate(CASES):
        raw = c["raw"] if c["raw"] is not None else json.dumps(c["payload"])
        r = subprocess.run(
            ["curl", "-s", "-m", "30", "-w", "\nHTTP_STATUS:%{http_code}",
             "-X", "POST", f"{base_url}/v1/actions/evaluate",
             "-H", "Content-Type: application/json", "--data-raw", raw],
            capture_output=True, text=True,
        )
        out = r.stdout
        if "HTTP_STATUS:" in out:
            body, status = out.rsplit("HTTP_STATUS:", 1)
            status = status.strip()
        else:
            body, status = out, "CURL_ERROR"
        results.append({"case": c["name"], "expect_4xx": c["expect_4xx"], "status": status,
                         "body_snippet": body.strip()[:200]})
        print(f"{i + 1}/{len(CASES)} {c['name'][:60]:60} -> {status}", file=sys.stderr)

    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    blocking = [r for r in results if r["status"].startswith("5")]
    unexpected = [
        r for r in results
        if not r["status"].startswith("5")
        and (r["expect_4xx"] and not r["status"].startswith("4"))
        or (not r["expect_4xx"] and not r["status"].startswith("2") and not r["status"].startswith("5"))
    ]
    print(f"\n{len(results)} cases run. {len(blocking)} BLOCKING (5xx). {len(unexpected)} unexpected.", file=sys.stderr)
    for r in blocking:
        print(f"  BLOCKING: {r['case']} -> {r['status']}: {r['body_snippet']}", file=sys.stderr)
    for r in unexpected:
        print(f"  UNEXPECTED: {r['case']} -> {r['status']} (expect_4xx={r['expect_4xx']})", file=sys.stderr)
    return 1 if (blocking or unexpected) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--out", default="fuzz_results.json")
    args = parser.parse_args()
    sys.exit(run(args.base_url.rstrip("/"), args.out))
