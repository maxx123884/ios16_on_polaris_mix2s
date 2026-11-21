#ifndef _GATE_CRYPTO_SHA2_H_
#define _GATE_CRYPTO_SHA2_H_
#include <stddef.h>
#include <stdint.h>
/*
 * XNU's SHA256_CTX is opaque to callers (0023 shows it wrapping a ccdigest
 * context). The host stub is a real SHA-256 whose state fits in this buffer.
 */
typedef struct { uint8_t opaque[224]; } SHA256_CTX;
void SHA256_Init(SHA256_CTX *ctx);
void SHA256_Update(SHA256_CTX *ctx, const void *data, size_t len);
void SHA256_Final(void *digest, SHA256_CTX *ctx);
#endif
