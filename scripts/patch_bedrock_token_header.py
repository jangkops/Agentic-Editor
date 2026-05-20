#!/usr/bin/env python3
"""Patch _invoke_bedrock_raw to capture x-amzn-bedrock-input-token-count and
x-amzn-bedrock-output-token-count headers, since some models (Cohere Embed)
don't include token counts in the body."""
import ast
from pathlib import Path

SRC = Path("/tmp/lambda-patch/handler.py.patched")
text = SRC.read_text(encoding="utf-8")

OLD = '''def _invoke_bedrock_raw(model_id, body_dict, timeout_sec=25):
    from botocore.config import Config as _Cfg
    cfg = _Cfg(retries={"max_attempts": 0}, read_timeout=timeout_sec, connect_timeout=10)
    client = boto3.client("bedrock-runtime", config=cfg)
    import json as _json
    payload = _json.dumps(body_dict).encode("utf-8")
    resp = client.invoke_model(modelId=model_id, body=payload,
                               contentType="application/json", accept="application/json")
    raw = resp["body"].read()
    try:
        return _json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        import base64 as _b64
        return {"_raw_b64": _b64.b64encode(raw).decode("ascii")}'''

NEW = '''def _invoke_bedrock_raw(model_id, body_dict, timeout_sec=25):
    from botocore.config import Config as _Cfg
    cfg = _Cfg(retries={"max_attempts": 0}, read_timeout=timeout_sec, connect_timeout=10)
    client = boto3.client("bedrock-runtime", config=cfg)
    import json as _json
    payload = _json.dumps(body_dict).encode("utf-8")
    resp = client.invoke_model(modelId=model_id, body=payload,
                               contentType="application/json",
                               accept="application/json")
    raw = resp["body"].read()
    # [INVOKE-ROUTE-v2] Capture token counts from headers (Cohere Embed etc)
    headers = resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    try:
        result = _json.loads(raw.decode("utf-8")) if raw else {}
        # Inject header-based token counts when body lacks them
        if isinstance(result, dict):
            in_tok = headers.get("x-amzn-bedrock-input-token-count")
            out_tok = headers.get("x-amzn-bedrock-output-token-count")
            if in_tok is not None and "inputTextTokenCount" not in result:
                try:
                    result["_bedrock_input_tokens"] = int(in_tok)
                except (TypeError, ValueError):
                    pass
            if out_tok is not None:
                try:
                    result["_bedrock_output_tokens"] = int(out_tok)
                except (TypeError, ValueError):
                    pass
        return result
    except Exception:
        import base64 as _b64
        return {"_raw_b64": _b64.b64encode(raw).decode("ascii")}'''

if OLD not in text:
    raise SystemExit("ERROR: _invoke_bedrock_raw block not found")
text = text.replace(OLD, NEW, 1)

# Also update _extract_invoke_usage to use _bedrock_input_tokens fallback
OLD2 = '''    if "embed" in raw:
        if isinstance(response_body, dict):
            # Titan Embed: inputTextTokenCount at top level
            n = response_body.get("inputTextTokenCount")
            if isinstance(n, (int, float)):
                usage["input_tokens"] = int(n)
                return usage
            # Cohere Embed: meta.billed_units.input_tokens
            meta = response_body.get("meta", {})
            bu = meta.get("billed_units", {}) if isinstance(meta, dict) else {}
            cn = bu.get("input_tokens")
            if isinstance(cn, (int, float)):
                usage["input_tokens"] = int(cn)
                return usage
            # Fallback: count texts in request × avg 256 tokens (rough estimate)
            # But fail-closed is safer — refuse
        raise ValueError("embedding response missing token count (inputTextTokenCount or meta.billed_units.input_tokens)")'''

NEW2 = '''    if "embed" in raw:
        if isinstance(response_body, dict):
            # Titan Embed: inputTextTokenCount at top level
            n = response_body.get("inputTextTokenCount")
            if isinstance(n, (int, float)):
                usage["input_tokens"] = int(n)
                return usage
            # Cohere Embed: meta.billed_units.input_tokens
            meta = response_body.get("meta", {})
            bu = meta.get("billed_units", {}) if isinstance(meta, dict) else {}
            cn = bu.get("input_tokens")
            if isinstance(cn, (int, float)):
                usage["input_tokens"] = int(cn)
                return usage
            # [INVOKE-ROUTE-v2] Fallback: header-injected count from Bedrock
            hn = response_body.get("_bedrock_input_tokens")
            if isinstance(hn, (int, float)):
                usage["input_tokens"] = int(hn)
                return usage
        raise ValueError("embedding response missing token count")'''

if OLD2 not in text:
    raise SystemExit("ERROR: embed extractor block not found")
text = text.replace(OLD2, NEW2, 1)

ast.parse(text)
SRC.write_text(text, encoding="utf-8")
print("Bedrock token header patch applied")
