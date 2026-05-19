"""
Contains the raw Python source code to be INSERTED into handler.py.
This module is imported by the patcher script to avoid f-string/brace issues.
"""

INVOKE_FUNCTIONS = r'''
# =============================================================================
# [INVOKE-ROUTE-v1] Synchronous /invoke — modal-aware InvokeModel proxy
# Added: 2026-05-19. Safe: never reaches SQS/SFN/Fargate.
# =============================================================================

_INVOKE_MODAL_RULES = [
    (lambda mid: mid.startswith("stability."), "image", "per_image"),
    (lambda mid: mid.startswith("amazon.titan-image"), "image", "per_image"),
    (lambda mid: mid.startswith("amazon.nova-canvas"), "image", "per_image"),
    (lambda mid: "embed" in mid, "embedding", "embedding_token"),
    (lambda mid: "rerank" in mid, "rerank", "rerank_doc"),
]


def _classify_invoke_modal(model_id):
    raw = model_id
    for prefix in ("us.", "eu.", "global."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    for pred, modal, unit in _INVOKE_MODAL_RULES:
        if pred(raw):
            return modal, unit
    return "text", "token"


def _extract_invoke_usage(model_id, request_body, response_body):
    raw = model_id
    for prefix in ("us.", "eu.", "global."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    usage = {"input_tokens": 0, "output_tokens": 0, "image_count": 0, "doc_count": 0}

    if raw.startswith("anthropic.claude"):
        u = response_body.get("usage", {}) if isinstance(response_body, dict) else {}
        usage["input_tokens"] = int(u.get("input_tokens", 0))
        usage["output_tokens"] = int(u.get("output_tokens", 0))
        if not usage["input_tokens"] and not usage["output_tokens"]:
            raise ValueError("anthropic response missing usage tokens")
        return usage

    if raw.startswith("stability."):
        imgs = (response_body.get("images") or response_body.get("artifacts") or []) if isinstance(response_body, dict) else []
        usage["image_count"] = max(1, len(imgs)) if imgs else 1
        return usage

    if raw.startswith("amazon.titan-image") or raw.startswith("amazon.nova-canvas"):
        imgs = response_body.get("images", []) if isinstance(response_body, dict) else []
        usage["image_count"] = max(1, len(imgs)) if imgs else 1
        return usage

    if "embed" in raw:
        if isinstance(response_body, dict):
            n = response_body.get("inputTextTokenCount")
            if isinstance(n, (int, float)):
                usage["input_tokens"] = int(n)
                return usage
        raise ValueError("embedding response missing inputTextTokenCount")

    if "rerank" in raw:
        if isinstance(request_body, dict):
            docs = request_body.get("documents") or []
            usage["doc_count"] = max(1, len(docs))
            return usage
        raise ValueError("rerank request missing documents")

    if raw.startswith("amazon.titan"):
        if isinstance(response_body, dict):
            usage["input_tokens"] = int(response_body.get("inputTextTokenCount", 0))
            for r in (response_body.get("results") or []):
                usage["output_tokens"] += int(r.get("tokenCount", 0))
            if usage["input_tokens"]:
                return usage
        raise ValueError("titan text response missing inputTextTokenCount")

    if raw.startswith("meta.llama"):
        if isinstance(response_body, dict):
            usage["input_tokens"] = int(response_body.get("prompt_token_count", 0))
            usage["output_tokens"] = int(response_body.get("generation_token_count", 0))
            if usage["input_tokens"] or usage["output_tokens"]:
                return usage
        raise ValueError("llama response missing token counts")

    if raw.startswith("mistral."):
        if isinstance(response_body, dict):
            u = response_body.get("usage", {})
            if u:
                usage["input_tokens"] = int(u.get("prompt_tokens", 0) or u.get("input_tokens", 0))
                usage["output_tokens"] = int(u.get("completion_tokens", 0) or u.get("output_tokens", 0))
                if usage["input_tokens"] or usage["output_tokens"]:
                    return usage
        raise ValueError("mistral response missing usage")

    if raw.startswith("cohere."):
        if isinstance(response_body, dict):
            meta = response_body.get("meta", {})
            bu = meta.get("billed_units", {}) if isinstance(meta, dict) else {}
            usage["input_tokens"] = int(bu.get("input_tokens", 0))
            usage["output_tokens"] = int(bu.get("output_tokens", 0))
            if usage["input_tokens"] or usage["output_tokens"]:
                return usage
        raise ValueError("cohere response missing meta.billed_units")

    raise ValueError("no usage extractor for model " + raw)


def _estimate_cost_krw_invoke(model_id, usage, pricing):
    unit = (pricing.get("pricing_unit") or "").strip() if isinstance(pricing, dict) else ""
    if unit in ("token", ""):
        return estimate_cost_krw(usage.get("input_tokens", 0), usage.get("output_tokens", 0), pricing)
    if unit == "embedding_token":
        return Decimal(str(usage.get("input_tokens", 0))) * pricing["input_price_per_1k"] / Decimal("1000")
    if unit == "per_image":
        n = usage.get("image_count", 0)
        pip = pricing.get("per_image_price_krw")
        if pip is None:
            raise ValueError("per_image_price_krw missing for " + model_id)
        return Decimal(str(n)) * Decimal(str(pip))
    if unit == "rerank_doc":
        n = usage.get("doc_count", 0)
        pdp = pricing.get("per_doc_price_krw")
        if pdp is None:
            raise ValueError("per_doc_price_krw missing for " + model_id)
        return Decimal(str(n)) * Decimal(str(pdp)) / Decimal("1000")
    raise ValueError("unknown pricing_unit: " + unit)


def _invoke_bedrock_raw(model_id, body_dict, timeout_sec=25):
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
        return {"_raw_b64": _b64.b64encode(raw).decode("ascii")}


def handle_invoke(principal_id, identity_fields, body, request_id):
    model_id = body.get("modelId", "")
    request_body = body.get("body", {})
    if not model_id:
        return deny_response("modelId is required", status_code=400)
    if not isinstance(request_body, dict):
        return deny_response("body must be a JSON object", status_code=400)

    try:
        cached = check_idempotency(request_id)
        if cached is not None:
            return _response(200, cached)
    except ConflictError:
        return _response(409, {"decision": "DENY", "denial_reason": "request already in progress"})

    if not create_idempotency_record(request_id, principal_id):
        return _response(409, {"decision": "DENY", "denial_reason": "request already in progress"})

    decision = "DENY"
    denial_reason = ""
    cost_krw = Decimal("0")
    usage = {"input_tokens": 0, "output_tokens": 0, "image_count": 0, "doc_count": 0}

    try:
        policy = lookup_principal_policy(principal_id)
        if not policy:
            denial_reason = "no policy defined for principal"
            return deny_response(denial_reason)

        if not check_model_access(policy, model_id):
            denial_reason = "model " + model_id + " not in allowed list"
            return deny_response(denial_reason)

        pricing = lookup_model_pricing(model_id)
        if not pricing:
            denial_reason = "no pricing defined for model " + model_id
            return deny_response(denial_reason)

        try:
            quota_result = check_quota(principal_id, policy)
        except Exception:
            denial_reason = "quota check failed"
            return deny_response(denial_reason)
        if not quota_result.get("allowed", False):
            return _response(429, {"decision": "DENY", "denial_reason": "monthly cost quota exceeded"})

        try:
            bedrock_response = _invoke_bedrock_raw(model_id, request_body, timeout_sec=25)
        except Exception as e:
            denial_reason = "bedrock invocation failed: " + str(e)[:200]
            log_structured("error", "invoke_bedrock_failed", request_id=request_id, model_id=model_id, error=str(e))
            return _response(502, {"decision": "ERROR", "error": denial_reason})

        try:
            usage = _extract_invoke_usage(model_id, request_body, bedrock_response)
        except ValueError as e:
            denial_reason = "usage extraction failed: " + str(e)[:160]
            log_structured("error", "invoke_usage_failed", request_id=request_id, error=str(e))
            return _response(502, {"decision": "ERROR", "error": denial_reason})

        try:
            cost_krw = _estimate_cost_krw_invoke(model_id, usage, pricing)
        except ValueError as e:
            denial_reason = "cost estimation failed: " + str(e)[:160]
            log_structured("error", "invoke_cost_failed", request_id=request_id, error=str(e))
            return _response(502, {"decision": "ERROR", "error": denial_reason})

        try:
            update_monthly_usage(principal_id, model_id, cost_krw,
                                 int(usage.get("input_tokens", 0)),
                                 int(usage.get("output_tokens", 0)))
        except Exception as e:
            log_structured("error", "invoke_usage_update_failed", request_id=request_id, error=str(e))

        decision = "ALLOW"
        return _response(200, {
            "decision": "ALLOW",
            "modelId": model_id,
            "output": bedrock_response,
            "usage": usage,
            "cost_krw": float(cost_krw),
        })
    finally:
        try:
            write_request_ledger({
                "request_id": request_id,
                "principal_id": principal_id,
                "model_id": model_id,
                "decision": decision,
                "denial_reason": denial_reason,
                "input_tokens": int(usage.get("input_tokens", 0)),
                "output_tokens": int(usage.get("output_tokens", 0)),
                "cost_krw": cost_krw,
                "source_path": "gateway-invoke-sync",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
'''

ROUTER_BLOCK = '''
    # --- [INVOKE-ROUTE-v1] Synchronous /invoke ---
    if path.rstrip("/") == "/invoke" and http_method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return deny_response("invalid JSON body", status_code=400)
        return handle_invoke(principal_id, identity_fields, body, request_id)

'''
