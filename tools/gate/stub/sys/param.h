#ifndef _GATE_SYS_PARAM_H_
#define _GATE_SYS_PARAM_H_
/*
 * The minimum <sys/param.h> polaris_gpu*.c rely on. This is a host stand-in,
 * not a copy of XNU's header: it only has to make the kernel sources compile
 * against the same types the kernel provides (bool, boolean_t, fixed widths).
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
/* 0097 gave polaris_gpu.c a vm_offset_t/uintptr_t surface (the probe maps two windows);
 * XNU's own <sys/param.h> drags these in, so the stand-in does too. */
#include <mach/vm_types.h>

typedef unsigned int boolean_t;

#ifndef __printflike
#define __printflike(fmtarg, firstvararg) \
	__attribute__((__format__(__printf__, fmtarg, firstvararg)))
#endif

#endif
