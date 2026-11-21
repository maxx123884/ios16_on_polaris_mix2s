/*
 * <string.h> stand-in for the kernel syntax check (tools/polaris_kernel_syntax.py).
 *
 * The kernel build does not ship its own <string.h>: makedefs/MakeInc.def
 * compiles with -nostdinc and the prototype comes from the macOS SDK. This
 * host has no SDK, so a check that mirrors that compile needs a header that
 * declares the same handful of functions. Only declarations live here -
 * nothing is implemented, and nothing in this file is compiled into a kernel.
 *
 * Keep this list minimal: everything below is called from XNU headers or from
 * bsd/dev/polaris_gpu*.c. A missing declaration shows up as -Wimplicit-
 * function-declaration, which is an error under the flags the check uses.
 */
#ifndef _PG_KERNEL_HEADERS_STRING_H_
#define _PG_KERNEL_HEADERS_STRING_H_

#include <stddef.h>

void *memcpy(void *, const void *, size_t);
void *memmove(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t);

size_t strlen(const char *);
size_t strnlen(const char *, size_t);
int strcmp(const char *, const char *);
char *strcpy(char *, const char *);

#endif /* _PG_KERNEL_HEADERS_STRING_H_ */
