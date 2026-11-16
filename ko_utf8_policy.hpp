#pragma once

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace strata::core::ko_utf8 {

constexpr uint8_t invalid = 255;

struct Table {
    uint32_t vocab = 0, states = 0;
    std::vector<int64_t> eos_ids;
    std::vector<uint8_t> next;
    std::vector<uint8_t> closing_distance;

    static Table load(const char* path, uint32_t expected_vocab, const std::vector<int64_t>& expected_eos) {
        if (!path || !*path) throw std::runtime_error("STRATA_KO_UTF8_TABLE is missing");
        std::ifstream file(path, std::ios::binary);
        auto word = [&]() {
            uint8_t raw[4];
            if (!file.read(reinterpret_cast<char*>(raw), 4))
                throw std::runtime_error("truncated UTF-8 policy header");
            return uint32_t(raw[0]) | (uint32_t(raw[1]) << 8) |
                   (uint32_t(raw[2]) << 16) | (uint32_t(raw[3]) << 24);
        };
        char magic[8];
        if (!file.read(magic, sizeof(magic)) || std::string(magic, sizeof(magic)) != std::string("KOUTF8\x01\x00", 8))
            throw std::runtime_error("invalid UTF-8 policy header");
        Table out;
        out.vocab = word();
        out.states = word();
        const uint32_t stops = word();
        if (out.vocab != expected_vocab || out.states == 0 || out.states > 63 ||
            stops == 0 || stops != expected_eos.size())
            throw std::runtime_error("UTF-8 policy, vocabulary or EOS dimensions differ");
        for (uint32_t i = 0; i < stops; ++i) {
            const uint32_t eos = word();
            if (eos >= out.vocab || (!out.eos_ids.empty() && out.eos_ids.back() >= eos))
                throw std::runtime_error("invalid UTF-8 policy EOS list");
            out.eos_ids.push_back(eos);
        }
        for (int64_t eos : expected_eos)
            if (!out.is_eos(eos)) throw std::runtime_error("UTF-8 policy EOS list differs from engine");
        out.next.resize(size_t(out.vocab) * out.states);
        if (!file.read(reinterpret_cast<char*>(out.next.data()), out.next.size()) || file.peek() != EOF)
            throw std::runtime_error("incorrect UTF-8 policy table length");
        std::vector<std::vector<bool>> targets(out.states, std::vector<bool>(out.states));
        for (uint32_t s = 0; s < out.states; ++s) {
            for (uint32_t token = 0; token < out.vocab; ++token) {
                const uint8_t n = out.advance(s, token);
                if (n >= out.states && n != invalid) throw std::runtime_error("invalid UTF-8 policy transition");
                if (out.is_eos(token)) {
                    if (n != (s == 0 ? 0 : invalid)) throw std::runtime_error("UTF-8 policy permits partial EOS");
                } else if (n != invalid) {
                    targets[s][n] = true;
                }
            }
        }
        out.closing_distance.assign(out.states, invalid);
        out.closing_distance[0] = 0;
        for (uint8_t budget = 1; budget <= 3; ++budget)
            for (uint32_t s = 1; s < out.states; ++s)
                if (out.closing_distance[s] == invalid)
                    for (uint32_t n = 0; n < out.states; ++n)
                        if (targets[s][n] && out.closing_distance[n] < budget) {
                            out.closing_distance[s] = budget;
                            break;
                        }
        if (std::find(out.closing_distance.begin(), out.closing_distance.end(), invalid) != out.closing_distance.end())
            throw std::runtime_error("cannot complete a UTF-8 state within three token steps");
        return out;
    }

    bool is_eos(int64_t token) const {
        for (int64_t eos : eos_ids) if (eos == token) return true;
        return false;
    }

    uint8_t advance(uint8_t state, int32_t token) const {
        if (state >= states || token < 0 || uint32_t(token) >= vocab) return invalid;
        return next[size_t(state) * vocab + token];
    }
};

struct Window {
    const Table* table;
    std::vector<uint8_t> masks;
};

// The final stage is called synchronously on the serial MTP verification thread.
// Prompt, unmasked requests, and unsupported parallel verification never inherit it.
inline thread_local const Window* active_window = nullptr;

class WindowScope {
public:
    explicit WindowScope(const Window* window) : previous_(active_window) { active_window = window; }
    ~WindowScope() { active_window = previous_; }
    WindowScope(const WindowScope&) = delete;
    WindowScope& operator=(const WindowScope&) = delete;
private:
    const Window* previous_;
};

inline Window preview(const Table& table, uint8_t committed, const int32_t* tokens, int count,
                      bool merged, int64_t produced, int64_t limit) {
    Window window{&table, {}};
    window.masks.reserve(count);
    uint8_t hypothetical = committed;
    for (int row = 0; row < count; ++row) {
        if (row != 0 && !merged)
            hypothetical = table.is_eos(tokens[row]) ? invalid : table.advance(hypothetical, tokens[row]);
        // Rows after an impossible draft are discarded by exact-match acceptance.
        const uint8_t state = hypothetical == invalid ? 0 : hypothetical;
        const int64_t remaining = std::clamp<int64_t>(limit - produced - (merged ? 0 : row) - 1, 0, 3);
        window.masks.push_back(state + remaining * table.states);
    }
    return window;
}

inline bool committed_state(const Table& table, uint8_t& state, const int32_t* chosen, int count,
                            int64_t& partial_pieces) {
    uint8_t next_state = state;
    for (int i = 0; i < count; ++i) {
        next_state = table.advance(next_state, chosen[i]);
        if (next_state == invalid) return false;
        partial_pieces += next_state != 0;
        if (table.is_eos(chosen[i])) break;
    }
    state = next_state;
    return true;
}

}  // namespace strata::core::ko_utf8
