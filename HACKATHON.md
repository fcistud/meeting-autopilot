# Project Description Required

Our entry is a local-first Meeting-to-Execution Autopilot that converts natural language meeting requests into immediate executable function calls. We designed a hybrid FunctionGemma + Gemini architecture where the default path is deterministic and on-device, then escalates only when confidence is insufficient. This creates strong privacy guarantees, low latency, and high reliability while still preserving frontier-model recovery for hard edge cases.

Innovation in local-first intelligence comes from a layered routing design in `main.py`: preprocessing, action splitting, schema-aware extraction, argument normalization, validation, deduplication, and quality scoring. FunctionGemma on Cactus is used as the primary intelligence engine on device, including cached model reuse (`cactus_init`, `cactus_reset`) to reduce cold-start overhead. The cloud path through Gemini (`google-genai`) is intentionally selective, not default, so user data remains local whenever high-confidence local execution is possible.

Technically, the system is implemented in Python with Cactus Compute, Google DeepMind FunctionGemma, optional Gemini function calling, and an objective benchmark harness that measures F1 quality, latency, and on-device ratio. The web demo integrates live voice-to-action behavior and routing observability, including plan preview before execution, confidence-based local/cloud visualization, and live metrics for latency and correctness.

This strategy redefines the edge-cloud frontier: it treats cloud as targeted escalation while maximizing local efficiency, privacy, and responsiveness. The result is an agentic system that balances frontier power with practical on-device performance and robust real-world action orchestration.

# Products & Tools Used Required

- Google DeepMind: **Used** (FunctionGemma and Gemini model stack)
- Cactus Compute: **Used** (on-device inference and speech pipeline primitives)
- AI Tinkerers: **Used** (community + hackathon support ecosystem)
- Other Products:
  - Python
  - Flask
  - `google-genai`
  - GitHub
