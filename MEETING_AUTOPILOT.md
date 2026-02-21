# Meeting-to-Execution Autopilot

Run a live voice-to-action demo with:
- `cactus_transcribe` meeting capture
- plan preview before execution
- confidence-based local/cloud fallback visualization
- live metrics (latency, on-device ratio, F1-style correctness)

## 1) Keep benchmark/submission baseline intact

Your benchmark solution remains `main.py` (unchanged), and can still be submitted with:

```bash
python3 submit.py --team "Mariam" --location "London"
```

## 2) One-time setup for transcription

```bash
cd cactus
source ./setup
cactus download openai/whisper-small
cd ..
```

## 3) Run the app

```bash
python3 meeting_autopilot_app.py
```

Open `http://127.0.0.1:8090`.

## 4) Demo flow

1. Click `Start Live Capture` and speak meeting actions.
2. Click `Generate Plan Preview` to inspect function calls before execution.
3. Review routing confidence bars (deterministic local, FunctionGemma local, Gemini cloud).
4. Click `Execute Previewed Plan` to run the action simulation.

## Notes

- Cloud escalation requires `GEMINI_API_KEY`.
- If cloud is disabled, the app stays local-first and picks the best local candidate.
- `Exact F1` appears when you provide expected calls in the Judge Mode JSON box.
