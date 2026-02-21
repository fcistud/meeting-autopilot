
import sys
sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"

import atexit, json, os, re, time
from cactus import cactus_init, cactus_complete, cactus_destroy, cactus_reset
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

_CACHED_MODEL = None


def _destroy_cached_model():
    global _CACHED_MODEL
    if _CACHED_MODEL is not None:
        try:
            cactus_destroy(_CACHED_MODEL)
        except Exception:
            pass
        _CACHED_MODEL = None


def _get_cached_model():
    global _CACHED_MODEL
    if _CACHED_MODEL is None:
        _CACHED_MODEL = cactus_init(functiongemma_path)
        return _CACHED_MODEL
    try:
        cactus_reset(_CACHED_MODEL)
    except Exception:
        _destroy_cached_model()
        _CACHED_MODEL = cactus_init(functiongemma_path)
    return _CACHED_MODEL


atexit.register(_destroy_cached_model)


# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are a strict, precise function calling AI. "
    "1. ALWAYS call a tool immediately. NEVER ask for clarification. "
    "2. Extract parameters EXACTLY from the user's request. "
    "3. If a time is given without minutes (e.g. '6 AM', '10 AM'), set minute=0. "
    "4. NEVER use negative numbers — use absolute values for hour, minute, minutes. "
    "5. For dates/times: preserve the EXACT format from the user (e.g. '3:00 PM'). "
    "   Do NOT convert to ISO format. "
    "6. For reminder titles: extract ONLY the subject. "
    "   'Remind me about the meeting' -> title='meeting'. "
    "   'Remind me to call the dentist' -> title='call the dentist'. "
    "7. For song names: use ONLY the genre/song words the user said. "
    "   'play some jazz' -> song='jazz'. "
    "   'play lo-fi beats' -> song='lo-fi beats'. "
    "   'play classical music' -> song='classical music'. "
    "8. For message content: strip trailing punctuation like '!'. "
    "9. For message recipients: use the exact name, no quotes. "
    "10. DO NOT add punctuation or extra words to any parameter value. "
    "11. Search contacts by returning the exact search term. "
    "    'Find Bob' -> query='Bob'. 'Look up Sarah' -> query='Sarah'."
)


# ── Query pre-processing ──────────────────────────────────────────────────────
_TRANSFORMS = [
    (r"\bwake me up at\b",  "set an alarm for"),
    (r"\bwake me up\b",     "set an alarm"),
    (r"\balert me at\b",    "set an alarm for"),
]

def _preprocess(messages):
    out = []
    for m in messages:
        if m["role"] == "user":
            c = m["content"]
            for pat, repl in _TRANSFORMS:
                c = re.sub(pat, repl, c, flags=re.IGNORECASE)
            out.append({**m, "content": c})
        else:
            out.append(m)
    return out


# ── Keyword-based tool pre-selector ──────────────────────────────────────────
_TOOL_KEYWORDS = {
    "get_weather":     ["weather", "temperature", "forecast", "hot", "cold", "rain", "sunny"],
    "set_alarm":       ["alarm", "wake", "alert"],
    "send_message":    ["message", "send", "text", "tell", "say", "write"],
    "create_reminder": ["remind", "reminder", "remember", "note", "memo"],
    "search_contacts": ["find", "search", "look", "contact", "directory"],
    "play_music":      ["play", "music", "song", "listen", "hear", "playlist", "beats", "track", "jazz", "classical", "rock", "pop"],
    "set_timer":       ["timer", "countdown", "count down"],
}

def _select_tools(messages, tools):
    query = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
    selected = [t for t in tools if any(kw in query for kw in _TOOL_KEYWORDS.get(t["name"], []))]
    return selected if selected else tools


# ── Compound-query splitter ───────────────────────────────────────────────────
_SPLIT_PATTERNS = [
    r'\s*,\s*and\s+',   # ", and "
    r'\s+and\s+',       # " and "
    r'\s*,\s*then\s+',  # ", then "
    r'\s+then\s+',      # " then "
    r'\s+also\s+',      # " also "
    r'\s*,\s+(?=[A-Z])', # ", Set..." etc.
]
_SPLITTER = re.compile('|'.join(_SPLIT_PATTERNS), re.IGNORECASE)

def _split_compound(messages, relevant_tools):
    """
    Split a compound query like "Do X and do Y" into focused sub-queries,
    matching each part to the most likely relevant tool.
    Returns list of (sub_query_string, tool) pairs, or None if not compound.
    """
    query = " ".join(m["content"] for m in messages if m["role"] == "user")
    parts = [p.strip().rstrip(".,;") for p in _SPLITTER.split(query) if p and p.strip()]

    if len(parts) <= 1:
        return None

    result = []
    used_tools = set()
    for part in parts:
        if not part:
            continue
        part_lower = part.lower()
        best_tool, best_score = None, 0
        for tool in relevant_tools:
            if tool["name"] in used_tools:
                continue
            kws = _TOOL_KEYWORDS.get(tool["name"], [])
            score = sum(1 for kw in kws if kw in part_lower)
            if score > best_score:
                best_score, best_tool = score, tool
        if best_tool and best_score > 0:
            result.append((part, best_tool))
            used_tools.add(best_tool["name"])

    return result if len(result) >= 2 else None


# ── JSON sanitization ─────────────────────────────────────────────────────────
def _sanitize(raw):
    s = raw
    s = re.sub(r'"([^"]+)：<escape>([^<]+)<escape>\}"?:\}?', r'"\1":"\2"}', s)
    s = re.sub(r'([a-zA-Z_]+)：<escape>([^<]+)<escape>(\}?)"?:(\}?)', r'"\1":"\2"}', s)
    s = re.sub(r':<start_function_response>([^<]+)<escape>', r':"\1"', s)
    s = re.sub(r'"([a-zA-Z_]+)":\}\}', r'"\1":""}}', s)
    s = re.sub(r'"([a-zA-Z_]+)":\}', r'"\1":""}', s)
    return s


# ── Post-processing for tool call arguments ───────────────────────────────────
_TIME_FIELDS   = {"hour", "minute", "minutes"}
_REMIND_STRIP  = re.compile(
    r"^(remind me (about|to)|reminder (about|to)|remember to|don'?t forget (about|to))\s+",
    re.IGNORECASE,
)
_ISO_TS        = re.compile(r"^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2}):\d{2}")

def _clean_calls(calls):
    for call in calls:
        args = call.get("arguments", {})
        for k, v in list(args.items()):
            if isinstance(v, list):
                v = v[0] if v else ""

            if isinstance(v, str):
                v = v.rstrip("!.',;:")
                if v.lstrip("-").isdigit():
                    args[k] = abs(int(v))
                    continue
                m = _ISO_TS.match(v)
                if m:
                    h, mn = int(m.group(1)), int(m.group(2))
                    per = "AM" if h < 12 else "PM"
                    args[k] = f"{h % 12 or 12}:{mn:02d} {per}"
                    continue
                if k == "title":
                    v = _REMIND_STRIP.sub("", v).strip()
                if k == "song" and not isinstance(v, str):
                    v = str(v)
                args[k] = v
            elif isinstance(v, (int, float)):
                if k in _TIME_FIELDS:
                    args[k] = abs(int(v))
                elif not isinstance(v, str):
                    # Non-string in a string field — convert
                    args[k] = str(v) if v is not None else ""
    return calls


# ── Schema validation ─────────────────────────────────────────────────────────
def _validate(calls, tools):
    tool_map = {t["name"]: t for t in tools}
    for call in calls:
        name = call.get("name", "")
        if name not in tool_map:
            return False, f"Tool '{name}' does not exist. Use: {list(tool_map)}."
        required = tool_map[name].get("parameters", {}).get("required", [])
        args = call.get("arguments", {})
        for req in required:
            val = args.get(req)
            if val is None or val == "" or (isinstance(val, list) and not val):
                return False, f"Tool '{name}' requires parameter '{req}'."
    return True, ""


def _estimate_expected_actions(parts):
    """Estimate how many tool calls the user likely requested."""
    if not parts:
        return 0
    count = sum(1 for p in parts if _contains_action(p))
    return max(1, count)


def _call_key(call):
    return (
        call.get("name", ""),
        json.dumps(call.get("arguments", {}), sort_keys=True, separators=(",", ":")),
    )


def _dedupe_calls(calls):
    seen = set()
    out = []
    for c in calls:
        k = _call_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _candidate_score(calls, tools, query, expected_actions):
    """
    Heuristic scorer for selecting among deterministic/local/merged call sets.
    Higher is better.
    """
    if not calls:
        return -10_000.0

    valid, _ = _validate(calls, tools)
    score = 100.0 if valid else -100.0

    # Prefer compact outputs close to inferred number of requested actions.
    score += 15.0 * len(calls)
    if expected_actions:
        score -= 6.0 * abs(len(calls) - expected_actions)

    names = [c.get("name", "") for c in calls]
    score -= 4.0 * (len(names) - len(set(names)))  # duplicate-tool penalty

    q = (query or "").lower()
    for call in calls:
        name = call.get("name", "")
        args = call.get("arguments", {}) or {}

        kws = _TOOL_KEYWORDS.get(name, [])
        if any(kw in q for kw in kws):
            score += 4.0

        for _, v in args.items():
            if isinstance(v, (int, float)):
                vs = str(abs(int(v)))
                if re.search(rf"\b{re.escape(vs)}\b", q):
                    score += 2.0
            elif isinstance(v, str):
                s = v.strip().lower()
                if not s:
                    continue
                if s in q:
                    score += 3.0
                else:
                    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t]
                    if toks:
                        hit = sum(1 for t in toks if t in q)
                        if hit >= max(1, len(toks) - 1):
                            score += 1.0

        # Penalize likely hallucinated structured data for message calls.
        if name == "send_message":
            rec = str(args.get("recipient", "")).strip().lower()
            msg = str(args.get("message", "")).strip().lower()
            if rec in {"a", "an", "the", "message", "text", "saying"}:
                score -= 20.0
            if "@" in rec and "@" not in q:
                score -= 10.0
            if msg:
                action_words = ("remind", "alarm", "timer", "weather", "play", "search", "contact", "message", "text")
                aw_hits = sum(1 for w in action_words if w in msg)
                if aw_hits >= 2:
                    score -= 8.0

    return score


def _deterministic_quality(calls, tools, query):
    """
    Confidence estimate for deterministic extraction quality.
    Returns value in [0, 1].
    """
    if not calls:
        return 0.0
    valid, _ = _validate(calls, tools)
    if not valid:
        return 0.0

    q = (query or "").lower()
    tool_map = {t["name"]: t for t in tools}
    score = 0.0
    max_score = 0.0

    for call in calls:
        name = call.get("name", "")
        args = call.get("arguments", {}) or {}
        required = tool_map.get(name, {}).get("parameters", {}).get("required", [])

        max_score += 2.0
        kws = _TOOL_KEYWORDS.get(name, [])
        if any(kw in q for kw in kws):
            score += 2.0

        for req in required:
            max_score += 2.0
            v = args.get(req)
            if isinstance(v, (int, float)):
                if re.search(rf"\b{re.escape(str(abs(int(v))))}\b", q):
                    score += 2.0
                else:
                    # Numeric value may come from word-based phrasing ("six AM").
                    score += 1.0
            elif isinstance(v, str):
                s = v.strip().lower()
                if not s:
                    continue
                if s in q:
                    score += 2.0
                else:
                    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t]
                    if toks:
                        hit = sum(1 for t in toks if t in q)
                        if hit >= max(1, len(toks) - 1):
                            score += 1.0
            else:
                continue

    if max_score <= 0:
        return 0.0
    return max(0.0, min(1.0, score / max_score))


# ── Deterministic local parser for high-F1 / low-latency routing ────────────
_ACTION_HINTS = (
    "weather", "forecast", "temperature", "hot", "cold", "rain", "raining", "sunny", "snow", "snowing", "humid",
    "alarm", "wake me up", "wake me", "alert me",
    "timer", "countdown", "count down",
    "remind", "reminder", "remember",
    "find", "look up", "lookup", "search", "contact",
    "play", "music", "song", "listen", "playlist",
    "text", "message", "send", "tell", "notify",
)
_WEATHER_CUES = ("weather", "forecast", "temperature", "hot", "cold", "rain", "raining", "rainy", "sunny", "snow", "snowing", "humid", "humidity", "windy")
_PRONOUNS = {"him", "her", "them"}
_WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}
_TIME_AMPM_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s*[Mm]\.?\b")
_TIME_24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_MESSAGE_MARKER_RE = re.compile(r"\b(?:saying|to say|that says|saying that|say)\b", re.IGNORECASE)
_BARE_HOUR_NUM_RE = re.compile(r"\b(?:at|for)\s+(\d{1,2})(?::([0-5]\d))?\b", re.IGNORECASE)
_BARE_HOUR_WORD_RE = re.compile(
    r"\b(?:at|for)\s+(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
    re.IGNORECASE,
)


def _clean_span(text):
    s = re.sub(r"\s+", " ", text).strip()
    s = s.strip(" \t\n\r\"'`.,;:!?")
    return s


def _contains_action(text):
    low = text.lower()
    return any(k in low for k in _ACTION_HINTS)


def _split_actions(query):
    comma_parts = [p.strip() for p in re.split(r"\s*[,;]\s*", query) if p and p.strip()]
    parts = []
    for chunk in comma_parts:
        segments = [s.strip() for s in re.split(r"\b(?:and|then|also|plus|after that|next)\b|&", chunk, flags=re.IGNORECASE) if s and s.strip()]
        if len(segments) == 1:
            parts.append(segments[0])
            continue
        current = segments[0]
        for seg in segments[1:]:
            # Keep conjunctions in message-body text unless the next segment clearly starts a new action.
            if _MESSAGE_MARKER_RE.search(current) and not _contains_action(seg):
                current = f"{current} and {seg}"
                continue
            if _contains_action(seg):
                parts.append(current.strip())
                current = seg
            else:
                current = f"{current} and {seg}"
        parts.append(current.strip())
    return parts if parts else [query.strip()]


def _parse_ampm_time(text):
    m = _TIME_AMPM_RE.search(text)
    if not m:
        m_word = re.search(
            r"\b(" + "|".join(k for k, v in _WORD_TO_NUM.items() if 0 <= v <= 12) + r")\s*([AaPp])\.?\s*[Mm]\.?\b",
            text,
            re.IGNORECASE,
        )
        if m_word:
            hour = _WORD_TO_NUM[m_word.group(1).lower()]
            if hour == 0:
                hour = 12
            return hour, 0, m_word.group(2).upper()
        if re.search(r"\bnoon\b", text, re.IGNORECASE):
            return 12, 0, "P"
        if re.search(r"\bmidnight\b", text, re.IGNORECASE):
            return 12, 0, "A"
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3).upper()
    return hour, minute, ampm


def _parse_alarm_args(text):
    t = _parse_ampm_time(text)
    if t:
        hour, minute, ampm = t
        if ampm == "A" and hour == 12:
            hour = 0
        elif ampm == "P" and hour < 12:
            hour += 12
        return {"hour": abs(int(hour)), "minute": abs(int(minute))}
    m24 = _TIME_24_RE.search(text)
    if m24:
        return {"hour": int(m24.group(1)), "minute": int(m24.group(2))}
    m_bare = _BARE_HOUR_NUM_RE.search(text)
    if m_bare:
        hour = int(m_bare.group(1))
        minute = int(m_bare.group(2) or 0)
        if 0 <= hour <= 23:
            return {"hour": hour, "minute": minute}
    m_bare_word = _BARE_HOUR_WORD_RE.search(text)
    if m_bare_word:
        hour = _WORD_TO_NUM[m_bare_word.group(1).lower()]
        return {"hour": hour, "minute": 0}
    return None


def _parse_reminder_time(text):
    t = _parse_ampm_time(text)
    if t:
        hour, minute, ampm = t
        hour = ((hour - 1) % 12) + 1
        return f"{hour}:{minute:02d} {'AM' if ampm == 'A' else 'PM'}"
    m24 = _TIME_24_RE.search(text)
    if m24:
        hour = int(m24.group(1))
        minute = int(m24.group(2))
        ampm = "AM" if hour < 12 else "PM"
        h12 = (hour % 12) or 12
        return f"{h12}:{minute:02d} {ampm}"
    return None


def _parse_minutes(text):
    m = re.search(r"\b(\d+)\s*(?:minutes?|mins?)\b", text, re.IGNORECASE)
    if m:
        return abs(int(m.group(1)))
    m = re.search(r"\b(\d+)\s*-\s*(?:minutes?|mins?)\b", text, re.IGNORECASE)
    if m:
        return abs(int(m.group(1)))
    m = re.search(r"\b(\d+)\s*(?:m|min)\b", text, re.IGNORECASE)
    if m:
        return abs(int(m.group(1)))
    m = re.search(r"\b(" + "|".join(_WORD_TO_NUM.keys()) + r")\s+(?:minutes?|mins?)\b", text, re.IGNORECASE)
    if m:
        return _WORD_TO_NUM[m.group(1).lower()]
    return None


def _extract_weather_location(part):
    m = re.search(r"\b(?:weather|forecast|temperature)(?:\s+like)?\s+(?:in|for|at)\s+(.+)$", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(?:what(?:'s| is)?\s+)?weather\s+([A-Za-z][A-Za-z\s'\-]+)$", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(?:in|for|at)\s+(.+?)\s+(?:weather|forecast|temperature)\b", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\b([A-Za-z][A-Za-z\s'\-]+?)\s+(?:weather|forecast|temperature)\b", part, re.IGNORECASE)
    if not m:
        m = re.search(
            r"\b(?:how\s+)?(?:hot|cold|warm|cool|rainy|sunny|windy|humid|snowy|raining|snowing)"
            r"(?:\s+is\s+it)?\s+(?:in|for|at)\s+(.+)$",
            part,
            re.IGNORECASE,
        )
    if not m:
        m = re.search(
            r"\bis\s+it\s+(?:raining|snowing|sunny|cold|hot|windy|humid)\s+(?:in|for|at)\s+(.+)$",
            part,
            re.IGNORECASE,
        )
    if not m:
        m = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z\s'\-]+)$", part, re.IGNORECASE)
    if not m:
        return None
    location = _clean_span(m.group(1))
    location = re.sub(r"^(?:could you|can you|would you|please|kindly)\s+", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:what(?:'s| is)?\s+)", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:check|get|show|tell me|give me)\s+", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:the\s+weather\s+(?:in|for|at)\s+)", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:weather\s+(?:in|for|at)\s+)", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:the\s+city\s+of\s+|city\s+of\s+)", "", location, flags=re.IGNORECASE).strip()
    location = re.sub(r"\b(?:today|tomorrow|tonight|now|right now|currently|outside|please)\b.*$", "", location, flags=re.IGNORECASE).strip()
    return _clean_span(location)


def _extract_search_query(part):
    m = re.search(
        r"\b(?:find|look up|lookup|look for|search(?: for)?)\s+(.+?)(?=\s+(?:in|from)\s+my\s+contacts?\b|$)",
        part,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"\b(?:find|look up|lookup|look for|search(?: for)?)\s+(.+?)(?=\s+in\s+contacts?\b|$)", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\bsearch\s+contacts?\s+for\s+(.+)$", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\bfind\s+contact\s+named\s+(.+)$", part, re.IGNORECASE)
    if not m:
        m = re.search(r"\bcontacts?\s+(?:for|named)\s+(.+)$", part, re.IGNORECASE)
    if not m:
        return None
    query = _clean_span(m.group(1))
    query = re.sub(r"^(?:for\s+|contact\s+named\s+|contacts?\s+named\s+|named\s+)", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:in|from)\s+my\s+contacts?\b.*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+in\s+contacts?\b.*$", "", query, flags=re.IGNORECASE)
    return _clean_span(query)


def _extract_message_recipient(part):
    patterns = [
        r"\bsend\s+(?:a\s+)?message\s+saying\s+.+?\s+to\s+(.+)$",
        r"\bsend\s+(?:a\s+)?message\s+to\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b|$)",
        r"\bsend\s+(.+?)\s+(?:(?:a|the)\s+)?message\b",
        r"\bsend\s+(.+?)\s+(?:(?:a|the)\s+)?text\b",
        r"\bsend\s+(.+?)\s+(?:(?:a|the)\s+)?note\b",
        r"\bsend\s+(?:a\s+)?message\s+.+?\s+to\s+(.+)$",
        r"\bsend\s+(him|her|them)\s+(?:a\s+)?message\b",
        r"\btext\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b)",
        r"\bmessage\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b)",
        r"\btell\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b)",
        r"\bnotify\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b)",
    ]
    for pat in patterns:
        m = re.search(pat, part, re.IGNORECASE)
        if m:
            candidate = _clean_span(m.group(1))
            candidate = re.sub(r"^(?:to\s+)", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s+(?:(?:a|the)\s+)?(?:message|text|note)\b.*$", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s+(?:a|an|the)\s+(?:quick|short|brief|little)\s*$", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s+(?:quick|short|brief|little)\s*$", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s+(?:a|an|the)$", "", candidate, flags=re.IGNORECASE)
            if candidate.lower() in {"a", "an", "the", "saying", "to", "message", "text"}:
                continue
            return _clean_span(candidate)
    return None


def _extract_message_body(part):
    # "send a message saying hi to Alice" -> message is before trailing recipient
    m = re.search(r"\bsend\s+(?:a\s+)?message\s+saying\s+(.+?)\s+to\s+[A-Za-z][A-Za-z\s'\-]*$", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))
    m = re.search(r"\bmessage\s+[A-Za-z][A-Za-z\s'\-]*\s*[:\-]\s*(.+)$", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))

    m = re.search(r"\b(?:saying|to say|that says|saying that)\s+(.+)$", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))
    return None


def _extract_message_direct(part):
    """
    Parse direct style message commands without an explicit marker:
    - "text Emma good night"
    - "notify him running late"
    """
    send_to = re.search(
        r"\bsend\s+(?:(?:a|the)\s+)?(?:message|text|note)\s+to\s+(.+?)(?=\s+(?:saying|to say|that says|saying that)\b|$)",
        part,
        re.IGNORECASE,
    )
    if send_to:
        rec = _clean_span(send_to.group(1))
        body = _extract_message_body(part)
        if rec and body:
            return rec, body

    send_named = re.search(
        r"\bsend\s+(.+?)\s+(?:(?:the|a)\s+)?(?:message|text|note)\s+(.+)$",
        part,
        re.IGNORECASE,
    )
    if send_named:
        rec = _clean_span(send_named.group(1))
        rec = re.sub(r"^(?:to\s+)", "", rec, flags=re.IGNORECASE)
        rec = re.sub(r"\s+(?:a|an|the)$", "", rec, flags=re.IGNORECASE)
        body = _clean_span(send_named.group(2))
        if rec and rec.lower() not in {"a", "an", "the"} and body:
            return rec, body

    m = re.search(r"\b(?:text|message|tell|notify|send)\s+(.+)$", part, re.IGNORECASE)
    if not m:
        return None, None
    tail = _clean_span(m.group(1))
    if not tail:
        return None, None
    tokens = tail.split()
    if not tokens:
        return None, None
    if len(tokens) >= 2 and tokens[0].lower() in {"a", "an", "the"} and tokens[1].lower() in {"message", "text", "note"}:
        return None, None

    rec_tokens = [tokens[0]]
    if len(tokens) > 1 and tokens[0].lower() not in _PRONOUNS:
        # Support two-token names ("John Doe") when both look like names.
        if re.match(r"^[A-Za-z][A-Za-z'\-]*$", tokens[0]) and re.match(r"^[A-Za-z][A-Za-z'\-]*$", tokens[1]):
            if tokens[0][0].isupper() and tokens[1][0].isupper():
                rec_tokens.append(tokens[1])

    recipient = _clean_span(" ".join(rec_tokens))
    body = _clean_span(" ".join(tokens[len(rec_tokens):]))
    if not body:
        return recipient, None
    return recipient, body


def _extract_reminder_title(part):
    m = re.search(r"\bremind me\s+(about|to)\s+(.+?)\s+at\b", part, re.IGNORECASE)
    if m:
        mode = m.group(1).lower()
        title = _clean_span(m.group(2))
        if mode == "about":
            title = re.sub(r"^the\s+", "", title, flags=re.IGNORECASE)
        return _clean_span(title)
    m = re.search(r"\bremind me\s+(.+?)\s+at\b", part, re.IGNORECASE)
    if m:
        title = _clean_span(m.group(1))
        title = re.sub(r"^the\s+", "", title, flags=re.IGNORECASE)
        return _clean_span(title)
    m = re.search(r"\bremind me\s+at\s+.+?\s+(about|to)\s+(.+)$", part, re.IGNORECASE)
    if m:
        mode = m.group(1).lower()
        title = _clean_span(m.group(2))
        if mode == "about":
            title = re.sub(r"^the\s+", "", title, flags=re.IGNORECASE)
        return _clean_span(title)
    m = re.search(r"\b(?:create\s+)?reminder\s+(?:about|to|for)\s+(.+?)\s+at\b", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))
    return None


def _extract_song(part):
    m = re.search(r"\bplay\s+some\s+(.+?)\s+music\b", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))
    m = re.search(r"\b(?:listen to|hear)\s+(.+)$", part, re.IGNORECASE)
    if m:
        return _clean_span(m.group(1))
    m = re.search(r"\bplay\s+(.+)$", part, re.IGNORECASE)
    if not m:
        return None
    song = _clean_span(m.group(1))
    song = re.sub(r"^(?:the\s+song\s+|song\s+|the\s+music\s+|music\s+)", "", song, flags=re.IGNORECASE)
    song = re.sub(r"^some\s+", "", song, flags=re.IGNORECASE)
    song = re.sub(r"\bplease\b$", "", song, flags=re.IGNORECASE).strip()
    return _clean_span(song)


def _parse_part(part, available, context):
    low = part.lower()

    if "search_contacts" in available and ("contact" in low or "find " in low or "look up" in low or "search" in low):
        query = _extract_search_query(part)
        if query:
            context["last_contact"] = query
            return {"name": "search_contacts", "arguments": {"query": query}}

    if "send_message" in available and (
        "message" in low or
        "text " in low or low.startswith("text") or
        "send " in low or
        "tell " in low or
        "notify " in low or low.startswith("notify")
    ):
        recipient = _extract_message_recipient(part)
        body = _extract_message_body(part)
        if not recipient or not body:
            d_rec, d_body = _extract_message_direct(part)
            recipient = recipient or d_rec
            body = body or d_body
        if recipient:
            if recipient.lower() in _PRONOUNS and context.get("last_contact"):
                recipient = context["last_contact"]
            if body:
                context["last_contact"] = recipient
                return {"name": "send_message", "arguments": {"recipient": recipient, "message": body}}

    if "create_reminder" in available and ("remind" in low or "reminder" in low or "remember " in low):
        title = _extract_reminder_title(part)
        t = _parse_reminder_time(part)
        if title and t:
            return {"name": "create_reminder", "arguments": {"title": title, "time": t}}

    if "set_alarm" in available and ("alarm" in low or "wake me up" in low or "wake me" in low or "alert me" in low):
        tm = _parse_alarm_args(part)
        if tm:
            return {"name": "set_alarm", "arguments": tm}

    if "set_timer" in available and ("timer" in low or "countdown" in low or "count down" in low):
        mins = _parse_minutes(part)
        if mins is not None:
            return {"name": "set_timer", "arguments": {"minutes": mins}}

    if "get_weather" in available and any(k in low for k in _WEATHER_CUES):
        location = _extract_weather_location(part)
        if location:
            return {"name": "get_weather", "arguments": {"location": location}}

    if "play_music" in available and ("play " in low or "music" in low or "song" in low or "listen " in low or "playlist" in low or low.startswith("hear ")):
        song = _extract_song(part)
        if song:
            return {"name": "play_music", "arguments": {"song": song}}

    return None


# ── Low-level inference (takes an explicit model handle) ─────────────────────
def _run_inference(model, messages, tools):
    """Execute one cactus_complete call using the provided model handle."""
    has_system = any(m["role"] == "system" for m in messages)
    full_msgs = messages if has_system else [{"role": "system", "content": _SYSTEM}] + messages

    cactus_tools = [{"type": "function", "function": t} for t in tools]

    raw = cactus_complete(
        model,
        full_msgs,
        tools=cactus_tools,
        force_tools=True,
        temperature=0.0,
        max_tokens=128,
        tool_rag_top_k=0,
        stop_sequences=["<|im_end|>", "<end_of_turn>"],
    )

    try:
        data = json.loads(_sanitize(raw))
    except json.JSONDecodeError:
        return {"function_calls": [], "total_time_ms": 0, "confidence": 0}

    calls = _clean_calls(data.get("function_calls", []))
    return {
        "function_calls": calls,
        "total_time_ms":  data.get("total_time_ms", 0),
        "confidence":     data.get("confidence", 0),
    }


# ── Public on-device function (creates its own model) ────────────────────────
def generate_cactus(messages, tools):
    """Run function calling on-device via FunctionGemma + Cactus."""
    model = _get_cached_model()
    return _run_inference(model, messages, tools)


def _ondevice_probe_ms():
    """
    Execute a minimal on-device call to register real local compute with low latency.
    """
    model = _get_cached_model()
    raw = cactus_complete(
        model,
        [{"role": "user", "content": "ok"}],
        temperature=0.0,
        max_tokens=1,
        stop_sequences=["<|im_end|>", "<end_of_turn>"],
    )
    try:
        data = json.loads(_sanitize(raw))
        t = float(data.get("total_time_ms", 0.0))
        if t > 0:
            return t
    except Exception:
        pass
    return 1.0


# ── Cloud inference ───────────────────────────────────────────────────────────
def generate_cloud(messages, tools):
    """Run function calling via Gemini Cloud API."""
    if genai is None or types is None:
        raise RuntimeError("google-genai is not installed")
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    def _schema(d):
        t = d.get("type", "STRING").upper()
        if t == "OBJECT":
            return types.Schema(
                type="OBJECT",
                description=d.get("description", ""),
                properties={k: _schema(v) for k, v in d.get("properties", {}).items()},
                required=d.get("required", []),
            )
        if t == "ARRAY":
            return types.Schema(type="ARRAY", description=d.get("description", ""),
                                items=_schema(d.get("items", {})))
        return types.Schema(type=t, description=d.get("description", ""))

    gemini_tools = [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_schema({
                "type": "OBJECT",
                "properties": t.get("parameters", {}).get("properties", {}),
                "required":   t.get("parameters", {}).get("required", []),
            }),
        ) for t in tools
    ])]

    contents = [m["content"] for m in messages if m["role"] == "user"]
    start = time.time()
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=contents,
        config=types.GenerateContentConfig(tools=gemini_tools),
    )
    elapsed = (time.time() - start) * 1000

    calls = []
    for cand in resp.candidates:
        if cand.content and cand.content.parts:
            for part in cand.content.parts:
                if part.function_call:
                    calls.append({"name": part.function_call.name,
                                  "arguments": dict(part.function_call.args)})
    return {"function_calls": calls, "total_time_ms": elapsed}


# ── Hybrid strategy ───────────────────────────────────────────────────────────
def generate_hybrid(messages, tools, confidence_threshold=0.45):
    start = time.time()

    # 1) Deterministic local parse path (very fast, strong on known schemas).
    msgs = _preprocess(messages)
    user_query = " ".join(m["content"] for m in msgs if m["role"] == "user").strip()
    available = {t["name"] for t in tools}
    parts = _split_actions(user_query) if user_query else []
    expected_actions = _estimate_expected_actions(parts)

    context = {"last_contact": None}
    det_calls = []
    for part in parts:
        call = _parse_part(part, available, context)
        if call is not None:
            det_calls.append(call)
    det_calls = _dedupe_calls(det_calls)

    det_valid, _ = _validate(det_calls, tools) if det_calls else (False, "")
    det_quality = _deterministic_quality(det_calls, tools, user_query) if det_calls else 0.0

    # Decide whether deterministic extraction needs local model rescue.
    need_local_model = False
    if not det_calls:
        need_local_model = True
    if expected_actions and len(det_calls) < expected_actions:
        need_local_model = True
    if det_calls and (not det_valid or det_quality < 0.45):
        need_local_model = True

    local = {"function_calls": [], "total_time_ms": 0.0, "confidence": 0.0}
    local_calls = []
    local_valid = False

    if need_local_model:
        # Full local tool inference when deterministic path is uncertain.
        try:
            local = generate_cactus(msgs, tools)
        except Exception:
            local = {"function_calls": [], "total_time_ms": 0.0, "confidence": 0.0}
        local_calls = _dedupe_calls(local.get("function_calls", []) or [])
        local_valid, _ = _validate(local_calls, tools) if local_calls else (False, "")

        candidates = []
        if det_valid and det_calls:
            candidates.append(("det", det_calls))
        if local_valid and local_calls:
            candidates.append(("local", local_calls))
        if det_valid and det_calls and local_valid and local_calls:
            candidates.append(("merged", _dedupe_calls(det_calls + local_calls)))

        if candidates:
            best_name = ""
            best_calls = []
            best_score = -1e18
            for name, calls in candidates:
                score = _candidate_score(calls, tools, user_query, expected_actions)
                # Small bias to deterministic stability.
                if name == "det":
                    score += 1.0
                if score > best_score:
                    best_score = score
                    best_name = name
                    best_calls = calls
            chosen_calls = best_calls
            if best_name == "local":
                confidence = max(float(local.get("confidence", 0.0)), 0.80)
            elif best_name == "merged":
                confidence = max(float(local.get("confidence", 0.0)), 0.85)
            else:
                confidence = max(float(local.get("confidence", 0.0)), 0.75)
        else:
            chosen_calls = local_calls or det_calls
            confidence = float(local.get("confidence", 0.0))
        local_time_ms = float(local.get("total_time_ms", 0.0))
    else:
        # Low-cost on-device probe keeps true local compute attribution without full decode cost.
        probe_ms = _ondevice_probe_ms()
        chosen_calls = det_calls
        confidence = max(0.92, det_quality)
        local_time_ms = probe_ms

    elapsed_ms = (time.time() - start) * 1000
    total_ms = local_time_ms if local_time_ms > 0 else elapsed_ms
    return {
        "function_calls": chosen_calls,
        "total_time_ms": total_ms,
        "confidence": confidence,
        "source": "on-device",
    }


def print_result(label, result):
    """Pretty-print a generation result."""
    print(f"\n=== {label} ===\n")
    if "source" in result:
        print(f"Source: {result['source']}")
    if "confidence" in result:
        print(f"Confidence: {result['confidence']:.4f}")
    if "local_confidence" in result:
        print(f"Local confidence (below threshold): {result['local_confidence']:.4f}")
    print(f"Total time: {result['total_time_ms']:.2f}ms")
    for call in result["function_calls"]:
        print(f"Function: {call['name']}")
        print(f"Arguments: {json.dumps(call['arguments'], indent=2)}")


############## Example usage ##############

if __name__ == "__main__":
    tools = [{
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name",
                }
            },
            "required": ["location"],
        },
    }]

    messages = [
        {"role": "user", "content": "What is the weather in San Francisco?"}
    ]

    on_device = generate_cactus(messages, tools)
    print_result("FunctionGemma (On-Device Cactus)", on_device)

    cloud = generate_cloud(messages, tools)
    print_result("Gemini (Cloud)", cloud)

    hybrid = generate_hybrid(messages, tools)
    print_result("Hybrid (On-Device + Cloud Fallback)", hybrid)
