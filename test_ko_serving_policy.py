import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ko_serving_policy import ACTIVE, RESULT, prepare


class KoreanServingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.path = Path(self.workspace.name, "policy-state")
        self.path.write_text("auto")
        environment = patch.dict(os.environ, STRATA_KO_POLICY_STATE=str(self.path))
        environment.start()
        self.addCleanup(environment.stop)

    def apply(self, request, text="한국어 안내문을 한 문장으로 작성해 주세요.", tools=None, engine=True):
        return prepare(request, [{"role": "user", "content": text}], tools, {"enable_thinking": True}, None,
                       engine_supported=engine)

    def test_plain_korean_defaults_to_native_policy(self):
        request = {}
        self.assertFalse(self.apply(request)["enable_thinking"])
        self.assertIs(request[ACTIVE], True)
        self.assertIs(request["strata_checkpoint"], False)
        self.assertEqual(request[RESULT], "on")

    def test_literal_and_tool_routes_preserve_output(self):
        for payload, text, tools, result in (
            ({}, "일본어로 써 주세요. 東京은 그대로 출력하세요.", None, "off/preserve"),
            ({"response_format": {"type": "json_object"}}, "자료를 정리해 주세요.", None, "off/structured-output"),
            ({}, "서울 날씨를 확인해 주세요.", [{"name": "weather"}], "off/tools"),
            ({"reasoning_effort": "high"}, "다음 정리를 증명해 주세요.", None, "off/explicit-thinking"),
        ):
            with self.subTest(result=result):
                self.assertTrue(self.apply(payload, text, tools)["enable_thinking"])
                self.assertNotIn(ACTIVE, payload)
                self.assertEqual(payload[RESULT], result)

    def test_operator_toggle_and_untrusted_internal_flag(self):
        req = {ACTIVE: True, RESULT: "on", "ko_output_policy": "off"}
        self.assertTrue(self.apply(req)["enable_thinking"])
        self.assertNotIn(ACTIVE, req)
        self.path.write_text("off")
        req = {}
        self.assertTrue(self.apply(req)["enable_thinking"])
        self.assertEqual(req[RESULT], "off")
        with self.assertRaisesRegex(ValueError, "operator disabled"):
            self.apply({"ko_output_policy": "on"})

    def test_explicit_on_only_for_supported_native_path(self):
        req = {"ko_output_policy": "on"}
        self.assertFalse(self.apply(req, "测试")["enable_thinking"])
        for candidate in ({"ko_output_policy": []}, {"ko_output_policy": "on", "logprobs": True}):
            with self.assertRaises(ValueError):
                self.apply(candidate)
        with self.assertRaisesRegex(ValueError, "engine-path"):
            self.apply({"ko_output_policy": "on"}, engine=False)


if __name__ == "__main__":
    unittest.main()
