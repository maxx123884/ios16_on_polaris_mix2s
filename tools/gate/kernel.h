#ifndef _GATE_KERNEL_H_
#define _GATE_KERNEL_H_
/* The host-side kernel services implemented by kernel.c. */
#include <stdint.h>

#include <sys/param.h>
#include <pexpert/device_tree.h>

int kprintf(const char *fmt, ...) __printflike(1, 2);
void klog_reset(void);
const char *klog_text(void);

void kset_bootarg(const char *name, int value);
void kadt_reset(void);
DTEntry kadt_node(const char *path);
void kadt_prop(DTEntry entry, const char *name, const void *data, unsigned int len);
void kmap_add(uint64_t pa, const void *va);
void kset_board(bool ok);

#endif
