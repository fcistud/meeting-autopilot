# Local-First Agentic System Walkthrough

## Submission Overview
This entry is a local-first, hybrid-ready agentic system built around FunctionGemma on Cactus Compute, with optional Gemini cloud escalation preserved as a capability layer. The central innovation is a deterministic edge intelligence pipeline that aggressively resolves tool calls on-device with near-zero orchestration overhead, then uses on-device model reasoning as a safety fallback when deterministic extraction detects ambiguity. The architecture is intentionally privacy-preserving and low-latency by default, while still exposing a frontier-model bridge for scenarios that truly require cloud-level reasoning.

The system targets the exact competition objective: maximize tool-call correctness, maximize on-device execution ratio, and minimize latency. In practice, the solution routes the overwhelming majority of requests locally, maintains strict tool-schema compliance, and preserves a robust fallback path so edge quality remains stable under paraphrases and multi-intent phrasing.

## What Was Built
The implementation centers on `generate_hybrid` in `main.py`, while preserving interface compatibility with the benchmark harness. The final design is a three-tier routing stack:

1. Deterministic local parser and action router.
2. FunctionGemma on-device fallback (`generate_cactus`) for uncertain or incomplete parses.
3. Gemini cloud escalation module (`generate_cloud`) retained as an optional frontier path.

This gives the system both deterministic precision and neural flexibility without compromising local-first execution.

## Innovation in Local-First Intelligence
The key innovation is treating edge intelligence as a first-class decision system, not only a cheaper inference target. Instead of asking a model to do every task from scratch, the system performs structured local understanding before model calls:

- Query normalization for colloquial variants.
- Multi-action segmentation for compound prompts.
- Tool-intent matching and argument extraction with strict schema-aware parsing.
- Conversational context carryover for pronoun resolution (for example, “find Tom … message him …”).
- Confidence-by-completeness logic that escalates only when local extraction is incomplete.

This architecture redefines the edge-cloud frontier by moving “reasoning about what to do” onto device-level logic, and reserving model inference for uncertainty handling rather than default execution.

## Hybrid FunctionGemma + Gemini Architecture
The entry is hybrid by construction and local-first by policy.

### Local Path (Primary)
- `generate_hybrid` runs deterministic parsing first.
- If extraction is complete and valid, it returns immediately with `source: "on-device"`.
- If parsing is incomplete or uncertain, it calls `generate_cactus` (FunctionGemma on device) for neural tool-call recovery.

### On-Device Neural Recovery (Secondary)
- `generate_cactus` initializes FunctionGemma via Cactus (`cactus_init`), runs tool-forced generation (`cactus_complete`), sanitizes malformed JSON patterns, normalizes arguments, and destroys model handles (`cactus_destroy`).
- This tier handles edge phrasing that deterministic patterns do not fully capture.

### Cloud Escalation (Optional Frontier Layer)
- `generate_cloud` is fully implemented with Google GenAI function declarations and schema mapping.
- The current competition routing policy is tuned to maximize local execution and privacy, so cloud invocation is not the default for benchmark-critical paths.
- The cloud layer remains production-ready and can be activated for broader open-domain workloads.

This gives judges a complete hybrid architecture: deterministic local intelligence + local neural fallback + cloud frontier capability.

## Technical Implementation Details
Core implementation file: `main.py`.

### Frameworks, Libraries, and Tools Used
- Python 3 runtime.
- Cactus Compute Python bindings:
  - `cactus_init`
  - `cactus_complete`
  - `cactus_destroy`
- Google DeepMind model stack:
  - FunctionGemma (on-device via Cactus weights).
  - Gemini Flash client path via `google-genai` SDK.
- Python standard libraries:
  - `re` for deterministic NLP extraction.
  - `json` for response decoding and cleanup.
  - `time` for latency accounting.
- Benchmark harness:
  - `benchmark.py` for objective F1/time/on-device scoring.

### Core Components Implemented
- Robust text preprocessing (`_preprocess`) to normalize variants like wake/alarm phrasing.
- Action decomposition (`_split_actions`) to split multi-intent prompts while preserving message-body clauses.
- Schema-safe argument extraction for each tool family:
  - Weather location extraction.
  - Alarm time extraction (AM/PM and 24h).
  - Timer duration extraction (digits, shorthand, and words).
  - Reminder title/time extraction.
  - Contact search extraction.
  - Music query extraction.
  - Messaging recipient/body extraction including direct style commands.
- Context memory (`last_contact`) for pronoun-based follow-up in multi-call requests.
- Validation layer (`_validate`) to enforce tool existence and required arguments.
- Uncertainty-aware local fallback:
  - If parser detects unresolved action segments, run FunctionGemma locally.
  - If neural output is valid, return it.
  - If neural output is invalid but deterministic calls are valid, return deterministic result.
- Optional cloud adapter with function schema conversion and guarded import behavior.

## On-Device Execution Strategy
The strategy is simple and aggressive:

- Keep routing local unless confidence-by-structure indicates risk.
- Treat deterministic completion as highest-confidence for known tools.
- Use FunctionGemma only when structural coverage is incomplete.
- Avoid cloud as default to preserve privacy, reduce tail latency, and maximize on-device ratio.

This produced an architecture that is both fast and robust in the constrained tool-calling domain.

## Cloud Escalation Strategy
Cloud escalation is implemented as a capability tier, not a mandatory dependency. The system includes:

- A complete Gemini function-calling adapter.
- Tool schema mapping to Google GenAI declarations.
- Safe runtime behavior when cloud SDK is unavailable.

Operationally, this means the system can run fully local for privacy-first and latency-sensitive use cases, while retaining a one-step path to frontier reasoning when product requirements justify network calls.

## Privacy and Low-Latency Optimization
Privacy and latency were design constraints, not afterthoughts.

### Privacy
- Default inference and routing happen on device.
- User messages do not need cloud transit for successful local calls.
- Sensitive tool arguments (contacts, reminders, messages) can remain local in normal operation.

### Latency
- Deterministic parsing avoids model invocation for straightforward tool intents.
- Local model fallback avoids network round-trips.
- Cloud is not in the hot path for benchmark-critical calls.

This pairing delivers practical agent responsiveness while minimizing data movement.

## Robustness Methodology
Robustness was expanded beyond canonical benchmark prompts with targeted hardening:

- Paraphrase tolerance for weather, reminder, timer, and messaging intents.
- Multi-intent phrase handling with punctuation and conjunction variants.
- Pronoun and contact carryover in chained commands.
- Defensive parsing and sanitization for malformed model JSON fragments.
- Required-argument validation before returning calls.

The result is a stable agentic controller that performs strongly on structured tool-calling tasks and remains resilient under realistic user phrasing variation.

## Judging Criteria Alignment
### Rubric 1: Hybrid routing algorithm quality and cleverness
The algorithm is not a naive threshold gate. It combines deterministic semantic extraction, schema validation, and selective neural fallback. This yields high precision, low orchestration cost, and explicit uncertainty handling.

### Rubric 2: End-to-end product utility
The system handles practical assistant workflows across weather, alarms, reminders, contacts, messaging, music, and timers, including compound requests and cross-reference pronouns.

### Rubric 3: Low-latency voice-to-action posture
The architecture is built for immediate action execution at the edge. Deterministic local intent extraction plus on-device FunctionGemma fallback enables a voice-to-tool path that is naturally low-latency and privacy-preserving.

## Compliance With Hackathon Requirements
- The solution modifies internal `generate_hybrid` logic while preserving signature compatibility.
- Output structure remains benchmark-compatible.
- Local-first execution is prioritized according to leaderboard incentives.
- Hybrid architecture is retained with a cloud capability layer in the same codebase.

## Why This Redefines the Edge-Cloud Frontier
This entry shifts intelligence allocation from “always ask a model” to “reason locally first, then escalate only when needed.” That inversion is the core frontier move: edge systems become active planners and validators, not passive model hosts. The practical outcome is a sophisticated agentic stack that preserves frontier extensibility while delivering local efficiency, privacy, and speed as default behavior.

## Products & Tools Used (Required)
### Used
- Google DeepMind: Yes.
- Cactus Compute: Yes.
- AI Tinkerers: Not directly used in implementation.

### Other Products Used
- Python 3.
- `google-genai` SDK.
- Regex-based deterministic parsing and validation pipeline.
- Git-based iteration workflow.
- Conductor multi-agent development workspace.

