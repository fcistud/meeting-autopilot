# Quick-Win Product Ideas (2-Hour Build) Using the Routing Algorithm

## Current Baseline (Locked)
- Robust 100% solution snapshot: `.context/backups/main.py.100_robust_final.20260221-141702.py`
- Revalidated benchmark result: `TOTAL SCORE: 100.0%`, `on-device: 100%`

## How to Maximize the 3 Rubrics

### Rubric 1: Routing Algorithm Depth and Cleverness
- Show a clear local-first decision graph:
  1. `cactus_transcribe` (speech -> text)
  2. deterministic parser + schema validator
  3. local FunctionGemma fallback for uncertain/partial parses
  4. optional Gemini escalation only when unresolved
- In demo UI/logs, display:
  - selected path (`deterministic`, `functiongemma-local`, `gemini-cloud`)
  - latency per stage
  - confidence/uncertainty flags

### Rubric 2: Real End-to-End Product with Function Execution
- Pick one domain with immediate real-world value.
- Demonstrate multi-step tool calls from one utterance.
- Show side effects (calendar/reminder creation, message dispatch, dashboard update, etc.).

### Rubric 3: Low-Latency Voice-to-Action with `cactus_transcribe`
- Make voice the default input path.
- Keep response local whenever possible.
- Measure and display speech-to-action completion time.

---

## Best 2-Hour Build Option (Recommended)
## 1) Voice Daily Ops Copilot
### One-line pitch
“A privacy-first voice copilot that executes reminders, timers, messaging, weather, and contact actions instantly on-device.”

### Why this is best
- Reuses your strongest existing tool schema directly.
- Naturally demonstrates single-call and multi-call action chains.
- Perfect for showing local speed + privacy + reliability.
- Minimal integration risk.

### Demo script (judge-friendly)
1. “Find Sara and text her saying I’m 10 minutes late.”
2. “Set a 15 minute timer and remind me to stretch at 4 PM.”
3. “Check weather in London and play focus music.”
4. “Wake me up at 6 AM and message Alex good night.”

### 2-hour MVP plan
- 0:00-0:30
  - Build tiny app loop: microphone/file input -> `cactus_transcribe` -> `generate_hybrid`.
- 0:30-1:00
  - Tool executor stubs with visible side effects (JSON action log + toast/output panel).
- 1:00-1:30
  - Routing telemetry panel (path, latency, source, fallback reason).
- 1:30-2:00
  - Polish script + short 60-90 sec demo recording.

### Rubric mapping
- R1: explicit hybrid routing telemetry
- R2: real multi-action function execution
- R3: live voice-to-action with local inference

---

## High-Upside Alternative
## 2) Emergency Family Safety Assistant
### One-line pitch
“Voice-triggered safety workflows: contact lookup, urgent messaging, location-aware weather checks, reminders, and timers in seconds.”

### Why it can stand out
- Strong emotional/practical value for judges.
- Multi-call sequences feel meaningful, not toy tasks.

### Demo commands
- “Find Mom and send her message saying I reached safely.”
- “Set a timer for 20 minutes and remind me to take medicine at 7 PM.”
- “Check weather in Seattle and remind me to carry an umbrella.”

### 2-hour scope
- Same engine as Daily Ops, different UI labels and prebuilt action templates.

---

## Creative Differentiator Option
## 3) Voice Meeting Runner
### One-line pitch
“A local voice assistant that runs meeting logistics: reminders, participant messaging, countdown timers, and contextual weather checks.”

### Why judges may like it
- Business-use framing.
- Naturally demonstrates chained calls and low-latency actioning.

### Demo commands
- “Remind me about standup at 10 AM and message the team saying I’ll join in 5.”
- “Set a 25 minute timer and play focus music.”

---

## Minimal Technical Stack for Any Option
- `cactus_transcribe` for ASR
- your `generate_hybrid` for tool routing
- existing tool schemas from `benchmark.py`
- tiny Python CLI or lightweight web UI (Flask/FastAPI + simple HTML)
- JSON action log for proof of execution

---

## Suggested Architecture for Demo Clarity
- Input layer:
  - audio file or mic capture
- Speech layer:
  - `cactus_transcribe(...)`
- Routing layer:
  - deterministic local parser
  - FunctionGemma local fallback
  - optional Gemini escalation
- Execution layer:
  - local tool handlers
- Observability:
  - per-step latencies
  - route selected
  - function calls emitted

---

## What to Show in Final Pitch
- “Default path is local for privacy and speed.”
- “Cloud is capability augmentation, not dependency.”
- “Voice intent to executed action in sub-second routing time on-device.”
- “Compound commands and paraphrases handled reliably.”

---

## If You Have Extra 30 Minutes
- Add a “Privacy Mode” toggle that forces no-cloud.
- Add transcript + function-call timeline view.
- Add saved “macros” (Morning Routine, Commute, Workout).

