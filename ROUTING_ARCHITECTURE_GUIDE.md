# Beginner Guide: How This Routing System Works

This document explains, in plain language, how the routing logic works in this project, why it was designed this way, and what tradeoffs were chosen.

If you are new to AI agents, tool calling, or hybrid local/cloud systems, this is for you.

## 1) What Problem Are We Solving?

A user says something like:

- "Remind me at 3 PM to call Alex."
- "Set a 10-minute timer and message Emma that I’m joining."
- "What’s the weather in Paris?"

The app must convert that natural language into **structured function calls** (tool calls) quickly and accurately.

At hackathon level, we optimize for three things at the same time:

1. Correctness (high F1 score)
2. Speed (low latency)
3. On-device ratio (stay local as much as possible)

## 2) Big Picture Architecture

There are two layers:

1. **Core router** in `main.py`
2. **Web product layer** in `meeting_autopilot_app.py` + `meeting_autopilot/static/app.js`

Core router objective:
- Given `messages` and `tools`, choose the best function calls.

Web app objective:
- Capture live audio -> transcribe -> preview plan -> execute actions.

### Core Route Path (high-level)

1. Preprocess text
2. Split into action parts
3. Try deterministic parsing first (fast path)
4. Validate quality and completeness
5. If uncertain, run local FunctionGemma inference
6. Score candidates (deterministic, local, merged)
7. Return best on-device plan

Cloud (Gemini) is available in the codebase, but this specific router design strongly prioritizes local behavior.

## 3) Most Important Function: `generate_hybrid`

File: `main.py`  
Signature: `generate_hybrid(messages, tools, confidence_threshold=0.45)`

This signature is kept compatible with the benchmark harness.

### Inputs

- `messages`: chat message list (typically includes user text)
- `tools`: allowed tool schemas (name + argument structure)
- `confidence_threshold`: threshold used for confidence decisions

### Output (shape)

It returns a dictionary:

- `function_calls`: list of selected calls
- `total_time_ms`: measured latency
- `confidence`: confidence estimate
- `source`: `"on-device"` in this design

## 4) Step-by-Step Flow Inside `generate_hybrid`

This is the exact logic, described simply.

### Step A: Normalize the text (`_preprocess`)

The router rewrites common phrasing to reduce ambiguity.

Example:
- "wake me up at 6" -> "set an alarm for 6"

Why:
- Normalized language makes parser rules more stable.

### Step B: Split one message into action parts (`_split_actions`)

A single sentence can contain multiple actions:

- "Set timer for 5 minutes and message Sam saying start now."

The splitter separates this into smaller chunks, while trying not to break message body text incorrectly.

Why:
- Multi-intent parsing is easier and more accurate per chunk.

### Step C: Parse each part deterministically (`_parse_part`)

For each chunk, rule-based extractors detect tool + arguments:

- alarms: hour/minute
- reminders: title/time
- messages: recipient/message
- timers: minutes
- weather: location
- music: song
- contacts: query

This uses helper functions like:

- `_extract_message_recipient`
- `_extract_message_body`
- `_extract_reminder_title`
- `_extract_weather_location`
- `_parse_alarm_args`
- `_parse_minutes`

Why:
- Deterministic parsing is fast and predictable.

### Step D: Validate calls (`_validate`)

Checks:

- tool exists in allowed schema
- required parameters exist and are non-empty

Why:
- Prevent invalid output from entering execution.

### Step E: Estimate quality (`_deterministic_quality`)

The router estimates confidence for deterministic output:

- does tool intent match words in query?
- do arguments align with user phrasing?

Why:
- Sometimes deterministic parser extracts something valid but not ideal.

### Step F: Decide if local model rescue is needed

Router triggers local model inference if:

- no deterministic calls were found, or
- not enough actions were captured, or
- deterministic quality is low, or
- validation fails

### Step G: Run local FunctionGemma (`generate_cactus`) when needed

`generate_cactus` uses Cactus + FunctionGemma on-device.

Important optimization:

- model is cached and reset (`cactus_init` once + `cactus_reset`) instead of full re-init every call.

Why:
- Much lower repeated latency.

### Step H: Build candidate sets and score them (`_candidate_score`)

Potential candidates:

1. deterministic only
2. local model only
3. merged deterministic + local

Scoring considers:

- schema validity
- action count alignment with expected actions
- duplicate penalties
- lexical alignment between arguments and user query
- hallucination penalties for suspicious message patterns

Why:
- One source is not always best. Scoring helps choose the safest final plan.

### Step I: Return final on-device response

The chosen plan is returned with timing/confidence and source metadata.

## 5) Why This Architecture Was Chosen

This system intentionally balances rule-based reliability with model flexibility.

### Choice 1: Deterministic-first routing

Benefit:
- very fast
- stable on common commands
- easy to debug

Tradeoff:
- rule coverage must be maintained for paraphrases

### Choice 2: Local model fallback

Benefit:
- handles edge cases where rules miss intent

Tradeoff:
- slower than deterministic parsing
- model output can need validation/sanitization

### Choice 3: Candidate scoring instead of single-source trust

Benefit:
- better resilience in ambiguous inputs

Tradeoff:
- more internal complexity

### Choice 4: Local-first by default

Benefit:
- privacy
- lower average latency
- high on-device ratio

Tradeoff:
- cloud-only reasoning power is not used by default

## 6) Key Optimizations

These are practical performance and quality optimizations in the current code.

### A) Cached on-device model lifecycle

- Keep FunctionGemma loaded
- reset state between requests

Result:
- lower latency than reloading model each time

### B) Query preprocessing

- normalize user phrasing before parsing

Result:
- higher extraction consistency

### C) Aggressive call cleaning/sanitization

- JSON cleanup
- type normalization
- argument cleanup

Result:
- fewer malformed tool calls

### D) Deterministic quality estimation

- confidence estimate before escalating to heavier steps

Result:
- lower compute cost on easy requests

### E) Candidate scoring and deduplication

- dedupe repeated calls
- score and choose best candidate set

Result:
- cleaner and more accurate final plans

## 7) Web App: How the Product Layer Works

The app is not just a benchmark runner; it is an end-to-end workflow.

### Frontend (`meeting_autopilot/static/app.js`)

Responsibilities:

1. Capture microphone audio
2. Build WAV chunks
3. Send chunks to `/api/transcribe`
4. Append transcript
5. Send transcript to `/api/route`
6. Show plan preview, confidence routing view, and metrics
7. Execute approved plan via `/api/execute`

Recent transcription improvements include:

- downsampling to 16kHz
- larger chunk windows
- overlap between chunks
- context carry-over prompt
- transcript stitching for boundary duplicates

### Backend (`meeting_autopilot_app.py`)

Responsibilities:

- `/api/health`: readiness checks
- `/api/transcribe`: calls `cactus_transcribe`
- `/api/route`: runs routing and returns plan + metrics
- `/api/execute`: simulates function execution

It also tracks session metrics:

- current and average latency
- on-device ratio
- F1-style proxy
- exact F1 when expected calls are provided

## 8) Technologies Used (and Why)

- **Python**: core routing and backend APIs
- **Cactus Compute**: on-device model runtime
- **FunctionGemma**: local tool-calling intelligence
- **Gemini (`google-genai`)**: optional cloud escalation path
- **Flask**: lightweight web server and APIs
- **Browser Web Audio APIs**: live microphone capture
- **Benchmark harness (`benchmark.py`)**: objective evaluation

## 9) Beginner Mental Model (Simple Version)

Think of this router like a smart dispatcher:

1. First, it tries a fast checklist method (rules).
2. If unsure, it asks a local AI model.
3. It compares possible plans and picks the safest one.
4. It returns structured actions your app can execute.

This is why it can be both fast and robust.

## 10) Tradeoffs You Should Know

No system is perfect. Here are honest tradeoffs:

- Rule-based systems are fast but need maintenance for new phrasing.
- Model fallback is flexible but can add latency.
- Strong local-first behavior improves privacy but may skip some cloud-only reasoning gains.
- More safety checks improve reliability but add implementation complexity.

These tradeoffs are intentional and aligned with the hackathon objective function.

## 11) How to Run the App

From repo root:

```bash
python3 meeting_autopilot_app.py
```

Open:

```text
http://127.0.0.1:8090
```

If Cactus is not linked in this workspace:

```bash
ln -s /Users/mariamhassan/functiongemma-hackathon/cactus cactus
```

If Whisper weights are missing:

```bash
cd cactus
source ./setup
cactus download openai/whisper-small
cd ..
```

## 12) How to Run Benchmarks

```bash
python3 benchmark.py
```

This gives:

- per-case F1
- per-case latency
- source attribution (on-device/cloud)
- total score

## 13) Quick FAQ

### Why not always use cloud?

Because local-first improves privacy and latency, and the hackathon explicitly rewards strong on-device performance.

### Why not always use deterministic rules?

Rules are great for common patterns, but real language has edge cases. The local model fallback catches many misses.

### Why validate and score instead of trusting first output?

Tool-calling quality is sensitive to small mistakes. Validation and scoring prevent brittle behavior.

### Is this production ready?

It is a strong hackathon-grade architecture and a solid product prototype. For production, you would add richer auth, real external tool integrations, and deeper observability.
