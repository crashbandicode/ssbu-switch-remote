// Streaming SHA-256 (FIPS 180-4), portable: the sysmodule and the host loopback build share it.
#pragma once
#include <cstddef>
#include <cstdint>

namespace sr {

class Sha256 {
public:
    Sha256() { reset(); }
    void reset();
    void update(const void* data, size_t n);
    void finish(uint8_t out[32]);
    // Lower-case hex of the digest; `out` holds 65 bytes (NUL included).
    void finish_hex(char out[65]);

private:
    void block(const uint8_t* p);
    uint32_t h_[8];
    uint8_t buf_[64];
    size_t len_ = 0;
    uint64_t total_ = 0;
};

}  // namespace sr
