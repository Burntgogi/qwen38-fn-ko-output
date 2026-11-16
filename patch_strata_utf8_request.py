"""Generate the isolated UTF-8 patch with request-local invariant failures."""


from patch_strata_utf8 import edit, generate_source, verify_source


def generate_request_source(path):
    return edit(generate_source(path), [
        ("            uint8_t utf8_state = 0;", """            uint8_t utf8_state = 0;
            bool utf8_rejected = false;"""),
        ("                if (req_ko_utf8 != \"1\" || geni", """                bool enabled = req_ko_utf8 == "1";
#if defined(STRATA_KO_UTF8_FAILURE_TEST)
                enabled = enabled || req_ko_utf8 == "probe_reject";
#endif
                if (!enabled || geni"""),
        ("""                    if (!strata::core::ko_utf8::committed_state(*utf8_table, utf8_state,
                                                               outv.data() + first, count, utf8_partial_pieces)) {
                        std::printf("ERR ko_utf8 rejected a selected token before emission\\n");
                        return 1;
                    }""", """                    bool reject = false;
#if defined(STRATA_KO_UTF8_FAILURE_TEST)
                    reject = req_ko_utf8 == "probe_reject";
#endif
                    if (reject || !strata::core::ko_utf8::committed_state(*utf8_table, utf8_state,
                                                                         outv.data() + first, count, utf8_partial_pieces)) {
                        // The rejected rows have neither been committed nor emitted.
                        utf8_rejected = true;
                        finish = "utf8_rejected";
                        break;
                    }"""),
        ("            if (!cancelled) {\n                // a prompt stopped halfway", """            if (utf8_rejected) {
                live.clear();
                live_imgs.clear();
                live_ok = false;
            } else if (!cancelled) {
                // a prompt stopped halfway"""),
        ("if (utf8_state != 0) finish = \"utf8_incomplete\";",
         'if (utf8_state != 0 && !utf8_rejected) finish = "utf8_incomplete";'),
    ])
