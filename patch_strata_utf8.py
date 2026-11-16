"""Patch the Strata CUDA engine at verified generation and sampling hooks."""



def edit(source, replacements):
    for original, replacement in replacements:
        if source.count(original) != 1:
            raise ValueError(f"expected one source match: {original[:110]!r}")
        source = source.replace(original, replacement, 1)
    return source


def verify_source(source):
    body = source.read_text(encoding="utf-8")
    return '#include "ko_utf8_policy.hpp"\n#include <limits>\n' + edit(body, [
        (
            "    if (le_ < g.n_layers) {   // a layer split's earlier stage: the hand-off is written (synced above)",
            "    const auto* utf8_window = strata::core::ko_utf8::active_window;\n"
            "    if (le_ < g.n_layers) {   // a layer split's earlier stage: the hand-off is written (synced above)",
        ),
        (
            """    if (head_sampling_ && (sampled || hist_d_ != nullptr || sampling_.logit_bias != nullptr)) {
        SamplerParams sp = sampling_;
        sp.counter = (uint64_t) pos0;
        // STRATA_SPEC_PROB:""",
            """    if (utf8_window && (!head_sampling_ || measure || spec_q_ != nullptr ||
                        utf8_window->masks.size() != size_t(T) || sampling_.logit_bias != nullptr)) {
        err = "unsupported UTF-8 verification path";
        return false;
    }
    if (head_sampling_ && (sampled || hist_d_ != nullptr || sampling_.logit_bias != nullptr || utf8_window)) {
        SamplerParams sp = sampling_;
        sp.counter = (uint64_t) pos0;
        // STRATA_SPEC_PROB:""",
        ),
        (
            """        if (judged) ++spec_windows;
        else sample_tokens(head_logits_, T, (int) n_vocab_, hist_d_, hist_len_, sp, m_out_, cs_);""",
            """        if (utf8_window) {
            using strata::core::ko_utf8::invalid;
            // Cache all rows on the final head's GPU. Ordinary windows make just one sampler call.
            static thread_local const strata::core::ko_utf8::Table* cached = nullptr;
            static thread_local float* device_bias = nullptr;
            static thread_local int bias_device = -1;
            const auto& table = *utf8_window->table;
            if (cached == nullptr) {
                const size_t row_count = 4 * table.states;
                std::vector<float> biases(row_count * table.vocab);
                for (size_t row = 0; row < row_count; ++row) {
                    const auto remaining = row / table.states;
                    for (uint32_t token = 0; token < table.vocab; ++token) {
                        const auto next = table.advance(row % table.states, token);
                        if (next == invalid || table.closing_distance[next] > remaining)
                            biases[row * table.vocab + token] = -std::numeric_limits<float>::infinity();
                    }
                }
                const size_t bytes = biases.size() * sizeof(float);
                if (cudaMalloc((void**) &device_bias, bytes) != cudaSuccess ||
                    cudaMemcpy(device_bias, biases.data(), bytes, cudaMemcpyHostToDevice) != cudaSuccess) {
                    if (device_bias) cudaFree(device_bias);
                    device_bias = nullptr;
                    err = "cannot upload the UTF-8 masks";
                    return false;
                }
                cached = &table;
                bias_device = device_;
            }
            if (cached != &table || bias_device != device_ || table.vocab != n_vocab_) {
                err = "UTF-8 mask, final head or vocabulary changed";
                return false;
            }
            for (int at = 0; at < T;) {
                const auto mask = utf8_window->masks[at];
                int end = at + 1;
                while (end < T && utf8_window->masks[end] == mask) ++end;
                sp.logit_bias = device_bias + size_t(mask) * table.vocab;
                sp.counter = uint64_t(pos0 + at);
                const int32_t* history = hist_d_ ? hist_d_ + size_t(at) * hist_len_ : nullptr;
                sample_tokens(head_logits_ + size_t(at) * table.vocab, end - at, (int) n_vocab_,
                              history, hist_len_, sp, m_out_ + at, cs_);
                at = end;
            }
        } else if (judged) ++spec_windows;
        else sample_tokens(head_logits_, T, (int) n_vocab_, hist_d_, hist_len_, sp, m_out_, cs_);""",
        ),
    ])


def generate_source(source):
    body = source.read_text(encoding="utf-8")
    return '#include "ko_utf8_policy.hpp"\n' + edit(body, [
        ("            bool req_bias_present = false;",
         "            bool req_bias_present = false;\n            std::string req_ko_utf8;"),
        ('                    else if (key == "pen_from")',
         '                    else if (key == "ko_utf8") req_ko_utf8 = tok.substr(eq + 1);\n'
         '                    else if (key == "pen_from")'),
        ("            std::string emb_path;\n            if (geni && endp != nullptr) {",
         """            const strata::core::ko_utf8::Table* utf8_table = nullptr;
            if (!req_ko_utf8.empty()) {
                if (req_ko_utf8 != "1" || geni || req_ckpt || req_prefill_only || req_pen_from >= 0 ||
                    req_bias_present || req_logprobs >= 0 || o.batch || use_df2 ||
                    (use_mtp && (mtp.coupled() || mtp.prob())) ||
                    std::getenv("STRATA_FOLLOW") != nullptr) {
                    std::printf("ERR ko_utf8 requires one-shot text and exact-match MTP without other bias or logprobs\\n");
                    continue;
                }
                try {
                    static const auto table = strata::core::ko_utf8::Table::load(
                        std::getenv("STRATA_KO_UTF8_TABLE"), (uint32_t) n_vocab, o.eos_ids);
                    utf8_table = &table;
                } catch (const std::exception& ex) {
                    std::printf("ERR ko_utf8: %s\\n", ex.what());
                    continue;
                }
            }
            uint8_t utf8_state = 0;
            int64_t utf8_partial_pieces = 0;
            std::string emb_path;
            if (geni && endp != nullptr) {"""),
        (': mtp.coupled() ? "coupled draft sampling" : nullptr;',
         ': mtp.coupled() ? "coupled draft sampling"\n'
         '                                  : utf8_table ? "UTF-8 output policy" : nullptr;'),
        (
            """                if (!ver.run(T, window.data(), p, decode_pool_fn, win_pool_user, outv.data(), err) || drive.d.failed) {""",
            """                const bool verified = [&] {
                    namespace utf8 = strata::core::ko_utf8;
                    if (!utf8_table)
                        return ver.run(T, window.data(), p, decode_pool_fn, win_pool_user, outv.data(), err);
                    auto masks = utf8::preview(*utf8_table, utf8_state, window.data(), T, merged, produced_n, max_new);
                    const utf8::WindowScope policy(&masks);
                    return ver.run(T, window.data(), p, decode_pool_fn, win_pool_user, outv.data(), err);
                }();
                if (!verified || drive.d.failed) {""",
        ),
        (
            "                if (from_sfx) { ++sfx_windows; sfx_drafts += T - 1; sfx_ok += a; }",
            """                if (utf8_table) {
                    const int first = merged ? a : 0;
                    const int count = (int) std::min<int64_t>(a - first + 1, max_new - produced_n);
                    if (!strata::core::ko_utf8::committed_state(*utf8_table, utf8_state,
                                                               outv.data() + first, count, utf8_partial_pieces)) {
                        std::printf("ERR ko_utf8 rejected a selected token before emission\\n");
                        return 1;
                    }
                }
                if (from_sfx) { ++sfx_windows; sfx_drafts += T - 1; sfx_ok += a; }""",
        ),
        (
            "            // DONE <generated> <prompt> <prompt ms> <decode ms> <finish> <drafts accepted> <drafts offered> <reused> [hits] [lookups]",
            """            if (utf8_table) {
                if (utf8_state != 0) finish = "utf8_incomplete";
                std::printf("KOUTF8 %u %lld\\n", (unsigned) utf8_state, (long long) utf8_partial_pieces);
            }
            // DONE <generated> <prompt> <prompt ms> <decode ms> <finish> <drafts accepted> <drafts offered> <reused> [hits] [lookups]""",
        ),
    ])
