"""Patch the Strata controller in a separate source tree."""

import ast

from patch_strata_utf8 import edit


def patch_controller(path):
    source = path.read_text(encoding="utf-8")
    source = edit(source, [
        ("from serve.structured import StructuredOutputError, prepare_format, validated_json, extract_json  # noqa: E402",
         "from serve.structured import StructuredOutputError, prepare_format, validated_json, extract_json  # noqa: E402\n"
         "from serve import ko_serving_policy  # noqa: E402"),
        ('        return keys + StrataEngine.projection_key(sampling) + bias_api.engine_key(sampling.get("logit_bias"))',
         '        if sampling.get(ko_serving_policy.ACTIVE) is True:\n'
         '            keys += " ko_utf8=1"\n'
         '        return keys + StrataEngine.projection_key(sampling) + bias_api.engine_key(sampling.get("logit_bias"))'),
        ('''        # Cached tool images are references in browser history, never arbitrary file paths or huge base64 strings.''',
         '''        kwargs = ko_serving_policy.prepare(req, messages, tools, kwargs, force,
            engine_supported=(isinstance(self.engine, StrataEngine) and not getattr(self.engine, "batch", 0)
                              and not getattr(self.engine, "split_engine", False)))
        # Cached tool images are references in browser history, never arbitrary file paths or huge base64 strings.'''),
        ('                        raise EngineNonFinite("the engine\'s logits became non-finite (#879)")\n                    return\n                elif line.startswith("ERR"):',
         '                        raise EngineNonFinite("the engine\'s logits became non-finite (#879)")\n'
         '                    if self.last.get("finish") in ("utf8_rejected", "utf8_incomplete"):\n'
         '                        raise ValueError("ko_output_policy: native generation did not complete safely")\n'
         '                    return\n                elif line.startswith("ERR"):'),
        ('        def end_headers(self):\n', '''        def end_headers(self):
            policy_status = getattr(self, "_ko_policy_status", None)
            if policy_status is not None:
                self.send_header("X-Strata-Ko-Policy", policy_status)
                self._ko_policy_status = None
'''),
    ])
    for name in ("openai", "responses", "anthropic"):
        original = f'            _debug_req("{name}", req, messages, tools, max_new, thinking, len(ids))'
        source = edit(source, [(original, '            self._ko_policy_status = req.get(ko_serving_policy.RESULT)\n' + original)])
    ast.parse(source, filename=str(path))
    return source
