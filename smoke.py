"""Check the policy headers and sample responses on a loopback Strata API."""

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from output_policy import allowed_text, is_han


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify(port, timeout, disabled):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    base = f"http://127.0.0.1:{port}"
    key = os.environ.get("STRATA_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def request(path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        with opener.open(Request(base + path, data=data, headers={**headers, "Content-Type": "application/json"}),
                         timeout=timeout) as response:
            return json.load(response), response.headers.get("X-Strata-Ko-Policy")

    models, _ = request("/v1/models")
    model = models["data"][0]["id"]

    def ask(prompt, mode="auto"):
        body, policy = request("/v1/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0,
            "max_tokens": 64, "stream": False, "ko_output_policy": mode,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        choice = body["choices"][0]
        return choice["message"].get("content") or "", choice["finish_reason"], policy

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    prompt = "다른 말 없이 ‘아홉 시’만 써 주세요."
    if disabled:
        _, _, policy = ask(prompt)
        require(policy == "off", f"expected off, got {policy!r}")
        try:
            ask(prompt, "on")
        except HTTPError as exc:
            if exc.code != 400:
                raise
        else:
            raise RuntimeError("expected HTTP 400 for forced on while the operator switch is off")
        print("PASS: auto is off and forced on is rejected")
        return

    text, finish, policy = ask(prompt)
    require(policy == "on" and finish == "stop" and "아홉 시" in text and allowed_text(text),
            f"Korean auto check failed (policy={policy!r}, finish={finish!r})")
    print("PASS: auto applies the mask and preserves 아홉 시")

    text, finish, policy = ask("‘東京’은 원문 그대로 두고 한국어로 짧게 답해 주세요.")
    require(policy == "off/preserve" and finish == "stop" and "東京" in text,
            f"original-spelling check failed (policy={policy!r}, finish={finish!r})")
    print("PASS: preservation request keeps 東京")

    chinese = "只输出四个汉字，不要解释：汉字测试"
    text, finish, policy = ask(chinese, "off")
    require(policy == "off" and finish == "stop" and any(is_han(ch) for ch in text),
            f"unconstrained control failed (policy={policy!r}, finish={finish!r})")
    text, finish, policy = ask(chinese, "on")
    require(policy == "on" and finish == "stop" and allowed_text(text),
            f"forced constraint check failed (policy={policy!r}, finish={finish!r})")
    print(f"PASS: forced on blocks Han; response is {'nonempty' if text.strip() else 'empty'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18095)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--disabled", action="store_true")
    args = parser.parse_args()
    try:
        verify(args.port, args.timeout, args.disabled)
    except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
