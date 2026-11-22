/*
 * Host stand-ins for the four kernel services bsd/dev/polaris_gpu*.c use:
 * printf, PE_parse_boot_argn, SecureDTLookupEntry/SecureDTGetProperty and
 * ml_io_map, plus the board predicate called from the gate.
 *
 * The point is to execute the real gate logic, not to imitate XNU. The ADT is
 * built by gate_main.c from the same six files and the same numbers the payload
 * carries, so a control-flow mistake in the driver shows up here.
 *
 * Prototypes deliberately mirror xnu-8796.141.3:
 *   pexpert/pexpert/pexpert.h        boolean_t PE_parse_boot_argn(const char *, void *, int)
 *   pexpert/pexpert/device_tree.h    int SecureDTGetProperty(const DTEntry, const char *, const void **, unsigned int *)
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <sys/param.h>
#include <mach/vm_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>

#include "kernel.h"

/* ---------------------------------------------------------------- printf */

static char g_log[16384];
static size_t g_log_len;

/* 0097's probe unmaps what it mapped; the harness keeps its maps listed. */
void
ml_io_unmap(vm_offset_t addr, vm_size_t size)
{
	(void)addr;
	(void)size;
}

void
klog_reset(void)
{
	g_log[0] = '\0';
	g_log_len = 0;
}

const char *
klog_text(void)
{
	return g_log;
}

int
kprintf(const char *fmt, ...)
{
	char line[1024];
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	if (n > 0 && g_log_len < sizeof(g_log) - 1) {
		size_t take = (size_t)n;

		if (take > sizeof(g_log) - 1 - g_log_len) {
			take = sizeof(g_log) - 1 - g_log_len;
		}
		memcpy(g_log + g_log_len, line, take);
		g_log_len += take;
		g_log[g_log_len] = '\0';
	}
	return n;
}

/* ------------------------------------------------------------- boot args */

#define KARGS 4

static struct {
	const char *name;
	int value;
} g_args[KARGS];
static unsigned int g_nargs;

void
kset_bootarg(const char *name, int value)
{
	unsigned int i;

	for (i = 0; i < g_nargs; ++i) {
		if (strcmp(g_args[i].name, name) == 0) {
			g_args[i].value = value;
			return;
		}
	}
	if (g_nargs < KARGS) {
		g_args[g_nargs].name = name;
		g_args[g_nargs].value = value;
		g_nargs++;
	}
}

boolean_t
PE_parse_boot_argn(const char *arg_string, void *arg_ptr, int max_arg)
{
	unsigned int i;

	for (i = 0; i < g_nargs; ++i) {
		if (strcmp(g_args[i].name, arg_string) != 0) {
			continue;
		}
		if (max_arg < (int)sizeof(int) || arg_ptr == NULL) {
			return 0;
		}
		memcpy(arg_ptr, &g_args[i].value, sizeof(int));
		return 1;
	}
	return 0;
}

/* ------------------------------------------------------------------- ADT */

#define KNODES 8
#define KPROPS 8

struct knode {
	const char *path;
	const char *names[KPROPS];
	const void *data[KPROPS];
	unsigned int len[KPROPS];
	unsigned int nprops;
};

static struct knode g_nodes[KNODES];
static unsigned int g_nnodes;

/*
 * Property data is copied into a static arena, the way a real device tree
 * stores values inline. Storing the caller's pointer would leave every entry
 * dangling as soon as the caller returned - which it did, and the resulting
 * garbage was read as a DRAM size.
 */
static unsigned char g_propdata[4096];
static size_t g_propdata_len;

void
kadt_reset(void)
{
	memset(g_nodes, 0, sizeof(g_nodes));
	g_nnodes = 0;
	g_propdata_len = 0;
}

DTEntry
kadt_node(const char *path)
{
	struct knode *n;

	if (g_nnodes >= KNODES) {
		return NULL;
	}
	n = &g_nodes[g_nnodes++];
	n->path = path;
	n->nprops = 0;
	return (DTEntry)(const void *)n;
}

void
kadt_prop(DTEntry entry, const char *name, const void *data, unsigned int len)
{
	struct knode *n = (struct knode *)(uintptr_t)entry;
	unsigned char *copy;

	if (n == NULL || data == NULL || n->nprops >= KPROPS ||
	    g_propdata_len + len > sizeof(g_propdata)) {
		return;
	}
	copy = &g_propdata[g_propdata_len];
	memcpy(copy, data, len);
	g_propdata_len += len;
	n->names[n->nprops] = name;
	n->data[n->nprops] = copy;
	n->len[n->nprops] = len;
	n->nprops++;
}

int
SecureDTLookupEntry(const DTEntry searchPoint, const char *pathName,
    DTEntry *foundEntry)
{
	unsigned int i;

	if (searchPoint != NULL || foundEntry == NULL) {
		return kError;
	}
	for (i = 0; i < g_nnodes; ++i) {
		if (strcmp(g_nodes[i].path, pathName) == 0) {
			*foundEntry = (DTEntry)(const void *)&g_nodes[i];
			return kSuccess;
		}
	}
	return kError;
}

int
SecureDTGetProperty(const DTEntry entry, const char *propertyName,
    const void **propertyValue, unsigned int *propertySize)
{
	const struct knode *n = (const struct knode *)(uintptr_t)entry;
	unsigned int i;

	if (n == NULL || propertyValue == NULL || propertySize == NULL) {
		return kError;
	}
	for (i = 0; i < n->nprops; ++i) {
		if (strcmp(n->names[i], propertyName) == 0) {
			*propertyValue = n->data[i];
			*propertySize = n->len[i];
			return kSuccess;
		}
	}
	return kError;
}

/* ------------------------------------------------------------- ml_io_map */

#define KMAPS 8

static struct {
	vm_offset_t pa;
	vm_offset_t va;
} g_maps[KMAPS];
static unsigned int g_nmaps;

void
kmap_add(uint64_t pa, const void *va)
{
	if (g_nmaps < KMAPS) {
		g_maps[g_nmaps].pa = (vm_offset_t)pa;
		g_maps[g_nmaps].va = (vm_offset_t)(uintptr_t)va;
		g_nmaps++;
	}
}

vm_offset_t
ml_io_map(vm_offset_t phys_addr, vm_size_t size)
{
	unsigned int i;

	(void)size;
	if (phys_addr == 0) {
		return 0;
	}
	for (i = 0; i < g_nmaps; ++i) {
		if (g_maps[i].pa == phys_addr) {
			return g_maps[i].va;
		}
	}
	/* An address the harness never registered must refuse, as XNU would. */
	return 0;
}

/* ----------------------------------------------------------------- board */

static bool g_board = true;

void
kset_board(bool ok)
{
	g_board = ok;
}

bool
polaris_touch_i2c_board(void)
{
	return g_board;
}
