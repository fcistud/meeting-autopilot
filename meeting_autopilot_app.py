#!/usr/bin/env python3
"""
Meeting-to-Execution Autopilot demo app.

Features:
- Live meeting transcription via cactus_transcribe (Whisper).
- Plan preview before execution.
- Confidence-based local/cloud routing visualization.
- Live metrics panel (latency, on-device ratio, F1-style correctness).
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from benchmark import compute_f1
from main import (
    _contains_action,
    _parse_part,
    _preprocess,
    _split_actions,
    _validate,
    generate_cactus,
    generate_cloud,
)

sys.path.insert(0, "cactus/python/src")
from cactus import cactus_destroy, cactus_init, cactus_transcribe


APP_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = APP_ROOT / "meeting_autopilot" / "templates"
STATIC_DIR = APP_ROOT / "meeting_autopilot" / "static"
TMP_AUDIO_DIR = APP_ROOT / ".context" / "tmp_audio"
TMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

WHISPER_MODEL_PATH = "cactus/weights/whisper-small"
WHISPER_PROMPT = "<|startoftranscript|><|en|><|transcribe|><|notimestamps|>"
DEFAULT_CONFIDENCE_THRESHOLD = 0.55


MEETING_TOOLS = [
    {
        "name": "create_reminder",
        "description": "Create a follow-up reminder from meeting decisions.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Reminder title"},
                "time": {"type": "string", "description": "Reminder time"},
            },
            "required": ["title", "time"],
        },
    },
    {
        "name": "send_message",
        "description": "Send an outbound message or update to stakeholders.",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Person to message"},
                "message": {"type": "string", "description": "Message text"},
            },
            "required": ["recipient", "message"],
        },
    },
    {
        "name": "set_timer",
        "description": "Set a timer for a follow-up action during or after the meeting.",
        "parameters": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Timer duration in minutes"},
            },
            "required": ["minutes"],
        },
    },
    {
        "name": "search_contacts",
        "description": "Run a research/contact lookup query from meeting context.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Lookup term"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_alarm",
        "description": "Set an alarm for urgent follow-up deadlines.",
        "parameters": {
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "description": "Hour in 24-hour format"},
                "minute": {"type": "integer", "description": "Minute"},
            },
            "required": ["hour", "minute"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get weather for travel planning decisions discussed in the meeting.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "Target location"},
            },
            "required": ["location"],
        },
    },
]


app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))

_session_lock = threading.Lock()
_session_metrics: dict[str, dict[str, float]] = {}

_whisper_lock = threading.Lock()
_whisper_model = None


def _session_id_from(payload: dict[str, Any]) -> str:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return "default-session"


def _get_whisper_model():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            _whisper_model = cactus_init(WHISPER_MODEL_PATH)
        return _whisper_model


@atexit.register
def _cleanup_models():
    global _whisper_model
    if _whisper_model is not None:
        cactus_destroy(_whisper_model)
        _whisper_model = None


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _schema_f1_proxy(calls: list[dict[str, Any]], tools: list[dict[str, Any]]) -> float:
    if not calls:
        return 0.0

    tool_map = {t["name"]: t for t in tools}
    per_call_scores = []
    valid_name_count = 0

    for call in calls:
        tool = tool_map.get(call.get("name"))
        if not tool:
            per_call_scores.append(0.0)
            continue

        valid_name_count += 1
        required = tool.get("parameters", {}).get("required", [])
        args = call.get("arguments", {})
        if not required:
            per_call_scores.append(1.0)
            continue

        filled = sum(1 for key in required if _is_non_empty(args.get(key)))
        per_call_scores.append(filled / len(required))

    precision_proxy = sum(per_call_scores) / len(per_call_scores)
    recall_proxy = valid_name_count / len(calls)
    if precision_proxy + recall_proxy == 0:
        return 0.0
    return (2 * precision_proxy * recall_proxy) / (precision_proxy + recall_proxy)


def _describe_call(call: dict[str, Any]) -> str:
    name = call.get("name", "")
    args = call.get("arguments", {})

    if name == "create_reminder":
        return f"Create reminder '{args.get('title', '')}' at {args.get('time', '')}"
    if name == "send_message":
        return f"Send message to {args.get('recipient', '')}: {args.get('message', '')}"
    if name == "set_timer":
        return f"Start a {args.get('minutes', '')}-minute timer"
    if name == "search_contacts":
        return f"Run research lookup for '{args.get('query', '')}'"
    if name == "set_alarm":
        hour = args.get("hour", 0)
        minute = args.get("minute", 0)
        return f"Set alarm for {int(hour):02d}:{int(minute):02d}"
    if name == "get_weather":
        return f"Check weather in {args.get('location', '')}"
    return f"Run {name} with {args}"


def _simulate_call(call: dict[str, Any]) -> dict[str, Any]:
    name = call.get("name", "")
    args = call.get("arguments", {})
    start = time.time()

    if name == "create_reminder":
        result = f"Reminder scheduled: '{args.get('title', '')}' at {args.get('time', '')}."
    elif name == "send_message":
        result = f"Message queued to {args.get('recipient', '')}."
    elif name == "set_timer":
        result = f"Timer started for {args.get('minutes', 0)} minutes."
    elif name == "search_contacts":
        result = f"Research query captured: '{args.get('query', '')}'."
    elif name == "set_alarm":
        result = f"Alarm armed for {int(args.get('hour', 0)):02d}:{int(args.get('minute', 0)):02d}."
    elif name == "get_weather":
        result = f"Weather check initiated for {args.get('location', '')}."
    else:
        result = "Tool executed."

    elapsed_ms = (time.time() - start) * 1000
    return {
        "tool": name,
        "arguments": args,
        "status": "success",
        "result": result,
        "time_ms": round(elapsed_ms, 2),
    }


def _deterministic_candidate(transcript: str) -> dict[str, Any]:
    msgs = _preprocess([{"role": "user", "content": transcript}])
    user_query = " ".join(m["content"] for m in msgs if m.get("role") == "user").strip()
    parts = _split_actions(user_query) if user_query else []
    available = {tool["name"] for tool in MEETING_TOOLS}
    context = {"last_contact": None}

    calls = []
    unparsed_action = False
    for part in parts:
        call = _parse_part(part, available, context)
        if call is not None:
            calls.append(call)
        elif _contains_action(part):
            unparsed_action = True

    valid = False
    if calls:
        valid, _ = _validate(calls, MEETING_TOOLS)

    coverage = (len(calls) / max(len(parts), 1)) if parts else 0.0
    confidence = 0.0
    if calls:
        confidence = 0.35 + (0.45 * coverage) + (0.2 if valid else 0.0)
        if unparsed_action:
            confidence *= 0.55
    confidence = max(0.0, min(0.98, confidence))

    return {
        "calls": calls,
        "parts": parts,
        "valid": valid,
        "coverage": coverage,
        "unparsed_action": unparsed_action,
        "confidence": confidence,
    }


def _route_plan(
    transcript: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    allow_cloud: bool = True,
) -> dict[str, Any]:
    start = time.time()
    messages = [{"role": "user", "content": transcript}]
    deterministic = _deterministic_candidate(transcript)

    stages = [
        {
            "id": "deterministic-local",
            "label": "Deterministic Local Parser",
            "confidence": round(deterministic["confidence"], 4),
            "status": "candidate" if deterministic["calls"] else "empty",
            "selected": False,
            "details": (
                "Covered all detected actions."
                if deterministic["calls"] and deterministic["valid"] and not deterministic["unparsed_action"]
                else "Partial or uncertain parse."
            ),
        },
        {
            "id": "functiongemma-local",
            "label": "FunctionGemma On-Device",
            "confidence": None,
            "status": "not-run",
            "selected": False,
            "details": "Executed only when deterministic confidence is low.",
        },
        {
            "id": "gemini-cloud",
            "label": "Gemini Cloud Escalation",
            "confidence": None,
            "status": "disabled" if not allow_cloud else "not-run",
            "selected": False,
            "details": "Escalates when local confidence is below threshold.",
        },
    ]

    selected_stage = "deterministic-local"
    selected_calls = deterministic["calls"]
    selected_confidence = deterministic["confidence"]
    selected_source = "on-device"
    reason = "Deterministic local parser had complete action coverage."

    deterministic_ready = (
        deterministic["calls"]
        and deterministic["valid"]
        and not deterministic["unparsed_action"]
        and deterministic["confidence"] >= confidence_threshold
    )

    if deterministic_ready:
        stages[0]["selected"] = True
        stages[0]["status"] = "selected"
        stages[1]["status"] = "skipped"
        if allow_cloud:
            stages[2]["status"] = "skipped"
    else:
        local = generate_cactus(messages, MEETING_TOOLS)
        local_calls = local.get("function_calls", [])
        local_confidence = float(local.get("confidence", 0.0) or 0.0)
        local_valid = False
        if local_calls:
            local_valid, _ = _validate(local_calls, MEETING_TOOLS)

        stages[1]["confidence"] = round(local_confidence, 4)
        stages[1]["status"] = "candidate" if local_calls else "empty"
        stages[1]["details"] = "Local model routing candidate."

        if local_calls and local_valid and local_confidence >= confidence_threshold:
            selected_stage = "functiongemma-local"
            selected_calls = local_calls
            selected_confidence = local_confidence
            selected_source = "on-device"
            reason = "FunctionGemma confidence cleared threshold."
            stages[1]["selected"] = True
            stages[1]["status"] = "selected"
            if allow_cloud:
                stages[2]["status"] = "skipped"
        elif allow_cloud:
            cloud_error = None
            cloud_calls = []
            cloud_valid = False
            cloud_confidence = 0.0
            try:
                cloud = generate_cloud(messages, MEETING_TOOLS)
                cloud_calls = cloud.get("function_calls", [])
                if cloud_calls:
                    cloud_valid, _ = _validate(cloud_calls, MEETING_TOOLS)
                cloud_confidence = 0.88 if (cloud_calls and cloud_valid) else 0.22
            except Exception as exc:  # pragma: no cover - depends on env keys/network
                cloud_error = str(exc)
                cloud_confidence = 0.0

            stages[2]["confidence"] = round(cloud_confidence, 4)
            stages[2]["details"] = "Cloud returned a valid routed plan." if cloud_valid else "Cloud fallback unavailable or invalid."

            if cloud_calls and cloud_valid:
                selected_stage = "gemini-cloud"
                selected_calls = cloud_calls
                selected_confidence = cloud_confidence
                selected_source = "cloud"
                reason = "Escalated to cloud due low local confidence."
                stages[2]["selected"] = True
                stages[2]["status"] = "selected"
            elif local_calls and local_valid:
                selected_stage = "functiongemma-local"
                selected_calls = local_calls
                selected_confidence = local_confidence
                selected_source = "on-device"
                reason = "Cloud unavailable; used valid on-device plan."
                stages[1]["selected"] = True
                stages[1]["status"] = "selected"
                stages[2]["status"] = "fallback-failed"
                if cloud_error:
                    stages[2]["details"] = f"Cloud error: {cloud_error}"
            elif deterministic["calls"] and deterministic["valid"]:
                selected_stage = "deterministic-local"
                selected_calls = deterministic["calls"]
                selected_confidence = deterministic["confidence"]
                selected_source = "on-device"
                reason = "Cloud and local candidate uncertain; using deterministic fallback."
                stages[0]["selected"] = True
                stages[0]["status"] = "selected"
                stages[2]["status"] = "fallback-failed"
                if cloud_error:
                    stages[2]["details"] = f"Cloud error: {cloud_error}"
            else:
                selected_stage = "deterministic-local"
                selected_calls = []
                selected_confidence = max(local_confidence, deterministic["confidence"])
                selected_source = "on-device"
                reason = "No confident routed actions were found."
                stages[2]["status"] = "fallback-failed"
                if cloud_error:
                    stages[2]["details"] = f"Cloud error: {cloud_error}"
        else:
            if local_calls and local_valid:
                selected_stage = "functiongemma-local"
                selected_calls = local_calls
                selected_confidence = local_confidence
                selected_source = "on-device"
                reason = "Cloud disabled; used best local candidate."
                stages[1]["selected"] = True
                stages[1]["status"] = "selected"
            elif deterministic["calls"] and deterministic["valid"]:
                selected_stage = "deterministic-local"
                selected_calls = deterministic["calls"]
                selected_confidence = deterministic["confidence"]
                selected_source = "on-device"
                reason = "Cloud disabled; deterministic fallback used."
                stages[0]["selected"] = True
                stages[0]["status"] = "selected"
            else:
                selected_stage = "deterministic-local"
                selected_calls = []
                selected_confidence = max(local_confidence, deterministic["confidence"])
                selected_source = "on-device"
                reason = "Cloud disabled and no confident local plan found."

    elapsed_ms = (time.time() - start) * 1000
    return {
        "function_calls": selected_calls,
        "source": selected_source,
        "confidence": round(selected_confidence, 4),
        "total_time_ms": round(elapsed_ms, 2),
        "route": {
            "selected_stage": selected_stage,
            "threshold": confidence_threshold,
            "allow_cloud": allow_cloud,
            "reason": reason,
            "stages": stages,
        },
    }


def _parse_expected_calls(raw_value: Any) -> list[dict[str, Any]] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _update_session_metrics(
    session_id: str,
    source: str,
    latency_ms: float,
    f1_style: float,
    exact_f1: float | None,
) -> dict[str, float]:
    with _session_lock:
        state = _session_metrics.setdefault(
            session_id,
            {
                "turns": 0.0,
                "on_device_turns": 0.0,
                "latency_sum_ms": 0.0,
                "f1_style_sum": 0.0,
                "exact_f1_sum": 0.0,
                "exact_f1_count": 0.0,
            },
        )

        state["turns"] += 1
        state["latency_sum_ms"] += latency_ms
        state["f1_style_sum"] += f1_style
        if source == "on-device":
            state["on_device_turns"] += 1
        if exact_f1 is not None:
            state["exact_f1_sum"] += exact_f1
            state["exact_f1_count"] += 1

        turns = max(state["turns"], 1.0)
        exact_count = max(state["exact_f1_count"], 1.0)

        return {
            "turns": int(state["turns"]),
            "latency_ms_current": round(latency_ms, 2),
            "latency_ms_avg": round(state["latency_sum_ms"] / turns, 2),
            "on_device_ratio": round((state["on_device_turns"] / turns) * 100, 1),
            "f1_style_current": round(f1_style, 3),
            "f1_style_avg": round(state["f1_style_sum"] / turns, 3),
            "exact_f1_current": None if exact_f1 is None else round(exact_f1, 3),
            "exact_f1_avg": (
                None
                if state["exact_f1_count"] == 0
                else round(state["exact_f1_sum"] / exact_count, 3)
            ),
        }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    whisper_exists = Path(WHISPER_MODEL_PATH).exists()
    return jsonify(
        {
            "ok": True,
            "whisper_model_path": WHISPER_MODEL_PATH,
            "whisper_weights_found": whisper_exists,
        }
    )


@app.post("/api/transcribe")
def api_transcribe():
    payload = request.get_json(silent=True) or {}
    audio_b64 = payload.get("audio_wav_base64")

    if not isinstance(audio_b64, str) or not audio_b64.strip():
        return jsonify({"ok": False, "error": "Missing 'audio_wav_base64'."}), 400

    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid base64 audio payload."}), 400

    audio_path = TMP_AUDIO_DIR / f"{uuid.uuid4().hex}.wav"
    audio_path.write_bytes(audio_bytes)

    try:
        model = _get_whisper_model()
        if model is None:
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "Whisper model is unavailable. Run: "
                        "'cd cactus && source ./setup && cactus download openai/whisper-small'"
                    ),
                }
            ), 500

        start = time.time()
        raw = cactus_transcribe(model, str(audio_path), prompt=WHISPER_PROMPT)
        elapsed_ms = (time.time() - start) * 1000

        data = json.loads(raw)
        transcript = (data.get("response") or "").strip()
        return jsonify(
            {
                "ok": True,
                "transcript": transcript,
                "total_time_ms": round(elapsed_ms, 2),
                "engine_time_ms": round(float(data.get("total_time_ms", 0.0)), 2),
            }
        )
    except Exception as exc:  # pragma: no cover - depends on runtime Cactus errors
        return jsonify({"ok": False, "error": f"Transcription failed: {exc}"}), 500
    finally:
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)


@app.post("/api/route")
def api_route():
    payload = request.get_json(silent=True) or {}
    transcript = payload.get("transcript", "")

    if not isinstance(transcript, str) or not transcript.strip():
        return jsonify({"ok": False, "error": "Transcript is required."}), 400

    session_id = _session_id_from(payload)
    threshold = payload.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    allow_cloud = bool(payload.get("allow_cloud", True))
    expected_calls = _parse_expected_calls(payload.get("expected_calls"))

    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_CONFIDENCE_THRESHOLD
    threshold = max(0.0, min(1.0, threshold))

    routed = _route_plan(transcript.strip(), confidence_threshold=threshold, allow_cloud=allow_cloud)
    calls = routed["function_calls"]
    f1_style = _schema_f1_proxy(calls, MEETING_TOOLS)
    exact_f1 = compute_f1(calls, expected_calls) if expected_calls is not None else None
    live_metrics = _update_session_metrics(
        session_id=session_id,
        source=routed["source"],
        latency_ms=float(routed["total_time_ms"]),
        f1_style=f1_style,
        exact_f1=exact_f1,
    )

    preview_steps = [
        {
            "index": idx + 1,
            "tool": call.get("name", ""),
            "arguments": call.get("arguments", {}),
            "description": _describe_call(call),
        }
        for idx, call in enumerate(calls)
    ]

    return jsonify(
        {
            "ok": True,
            "transcript": transcript.strip(),
            "plan": calls,
            "preview_steps": preview_steps,
            "source": routed["source"],
            "confidence": routed["confidence"],
            "total_time_ms": routed["total_time_ms"],
            "route": routed["route"],
            "f1_style": round(f1_style, 3),
            "exact_f1": None if exact_f1 is None else round(exact_f1, 3),
            "live_metrics": live_metrics,
        }
    )


@app.post("/api/execute")
def api_execute():
    payload = request.get_json(silent=True) or {}
    plan = payload.get("plan")
    if not isinstance(plan, list):
        return jsonify({"ok": False, "error": "Plan must be a list of function calls."}), 400

    results = [_simulate_call(call) for call in plan]
    return jsonify(
        {
            "ok": True,
            "executed_count": len(results),
            "results": results,
        }
    )


if __name__ == "__main__":
    debug = os.environ.get("AUTOPILOT_DEBUG", "0") == "1"
    app.run(host="127.0.0.1", port=8090, debug=debug, use_reloader=False)
