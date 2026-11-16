"""Request-local Korean policy for Strata's plain-text HTTP generation."""

import os
from pathlib import Path
import re


ACTIVE = "_strata_ko_utf8_active"
RESULT = "_strata_ko_utf8_result"
VALID_MODES = frozenset(("auto", "on", "off"))
HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003347f]")
KO_REQUEST = re.compile(r"(?:answer|respond|reply|write|explain)\s+in\s+Korean", re.I)
PRESERVE = re.compile(
    r"```|`[^`\n]+`|(?:\bJSON\b|\bYAML\b|\bXML\b|\bCSV\b|\bSQL\b)\s*(?:만|로|형식)|"
    r"(?:원문|원래 표기|인용문|파일명|경로|코드|철자).{0,14}(?:그대로|정확히|보존|생성|작성|출력)|"
    r"(?:한자|중국어|일본어|영어|한문|영문)(?:만|로)\s*(?:만\s*)?(?:답|써|쓰|작성|번역|설명|출력|표기)|"
    r"(?:중국어|일본어|영어|한문|영문)\s*(?:답변|번역|출력)|"
    r"(?:그대로|따옴표까지)\s*(?:복사|인용|표기|출력)|"
    r"\b(?:respond|answer|reply|write)\s+in\s+(?:Chinese|Japanese|English)\b",
    re.I,
)


def enabled_by_operator():
    path = os.environ.get("STRATA_KO_POLICY_STATE")
    if not path:
        return False
    try:
        return Path(path).read_text(encoding="ascii").strip() == "auto"
    except (OSError, UnicodeError):
        return False


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if not isinstance(part, dict) or part.get("type") not in ("text", "input_text", "output_text"):
                return None
            text = part.get("text")
            if not isinstance(text, str):
                return None
            parts.append(text)
        return "\n".join(parts)
    if value is None:
        return ""
    return None


def explicitly_thinking(req):
    effort = req.get("reasoning_effort")
    reasoning = req.get("reasoning")
    if isinstance(reasoning, dict) and any(reasoning.get(key) is not None for key in ("effort", "summary")):
        if reasoning.get("effort") != "none" or reasoning.get("summary") is not None:
            return True
    thinking = req.get("thinking")
    template = req.get("chat_template_kwargs")
    return ((effort is not None and effort != "none") or req.get("enable_thinking") is True or
            (isinstance(thinking, dict) and thinking.get("type") != "disabled") or
            (isinstance(template, dict) and template.get("enable_thinking") is True) or
            bool(req.get("reasoning_budget_tokens")))


def incompatible(req, messages, tools, force, engine_supported):
    if not engine_supported:
        return "engine-path"
    if (tools or force or req.get("tools") or req.get("strata_mcp") is True or req.get("deferred_tools") or
            any(msg.get("tool_calls") or msg.get("role") == "tool" for msg in messages)):
        return "tools"
    fmt = req.get("response_format")
    txt = req.get("text")
    if isinstance(txt, dict) and txt.get("format") is not None:
        fmt = txt["format"]
    if fmt and (not isinstance(fmt, dict) or fmt.get("type") != "text"):
        return "structured-output"
    if (req.get("logprobs") or req.get("top_logprobs") is not None or req.get("logit_bias") or
            req.get("strata_prefix") or req.get("strata_checkpoint") is True):
        return "native-options"
    if explicitly_thinking(req):
        return "explicit-thinking"
    if any(content_text(msg.get("content")) is None for msg in messages):
        return "non-text-input"
    return None


def prepare(req, messages, tools, kwargs, force, *, engine_supported):
    """Classify the request and return a new template settings dictionary only when active."""
    if req is None:
        return kwargs
    req.pop(ACTIVE, None)  # Internal state never comes from the HTTP request.
    req.pop(RESULT, None)
    mode = req.get("ko_output_policy", "auto")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise ValueError("ko_output_policy: expected auto, on, or off")
    if mode == "off" or not enabled_by_operator():
        if mode == "on":
            raise ValueError("ko_output_policy: operator disabled the policy")
        req[RESULT] = "off"
        return kwargs
    blocked = incompatible(req, messages, tools, force, engine_supported)
    if blocked:
        if mode == "on":
            raise ValueError(f"ko_output_policy: unavailable for {blocked}")
        req[RESULT] = f"off/{blocked}"
        return kwargs
    user = next((content_text(msg.get("content")) or "" for msg in reversed(messages) if msg.get("role") == "user"), "")
    system = "\n".join(content_text(msg.get("content")) or "" for msg in messages if msg.get("role") in ("system", "developer"))
    if mode == "auto":
        if not (HANGUL.search(user) or KO_REQUEST.search(user)):
            req[RESULT] = "off/language"
            return kwargs
        if HAN.search(user) or PRESERVE.search(user) or PRESERVE.search(system):
            req[RESULT] = "off/preserve"
            return kwargs
    req[ACTIVE] = True
    req[RESULT] = "on"
    req["strata_checkpoint"] = False
    return {**kwargs, "enable_thinking": False}
