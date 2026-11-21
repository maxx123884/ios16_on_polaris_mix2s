/*
 * Executes polaris_gpu_init() on the host with the real pinned firmware.
 *
 * The kernel file is an all-or-nothing gate: board identity, ADT layout,
 * pinned sizes, digests, then a refusal at the first stage without a backend.
 * Reading it proves nothing about the order in which those checks actually run,
 * so this harness builds the same ADT the payload produces, maps the six files
 * with the addresses the payload uses, and asserts on what the gate reported.
 *
 * What it does NOT prove: that the sources compile against the real XNU headers
 * (the stubs in ./stub are deliberately minimal), or anything about the device.
 *
 *   gate <firmware-dir>            run every scenario
 *   gate <firmware-dir> <name>     run one
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libkern/crypto/sha2.h>

#include "kernel.h"

void polaris_gpu_init(void);

#define BLOBS 6
#define DRAM_BASE 0x100000000ull
#define DRAM_SIZE 0x7CC00000ull	/* 1.95 GiB: the managed upper range */

struct blob_fixture {
	const char *map_name;
	const char *file;
	uint64_t pa;		/* 16 KiB aligned, as the payload places them */
};

static const struct blob_fixture fixtures[BLOBS] = {
	{ "GPU-SQE",     "a630_sqe.fw",  0x0102000000ull },
	{ "GPU-GMU",     "a630_gmu.bin", 0x0102008000ull },
	{ "GPU-ZAP-MDT", "a630_zap.mdt", 0x0102010000ull },
	{ "GPU-ZAP-B00", "a630_zap.b00", 0x0102018000ull },
	{ "GPU-ZAP-B01", "a630_zap.b01", 0x0102020000ull },
	{ "GPU-ZAP-B02", "a630_zap.b02", 0x0102028000ull }
};

static unsigned char *g_data[BLOBS];
static size_t g_size[BLOBS];
static uint64_t g_pa[BLOBS];
static uint64_t g_adt_size[BLOBS];
static unsigned int g_failures;

static void
put_le64(void *dst, uint64_t v)
{
	unsigned char *p = dst;
	unsigned int i;

	for (i = 0; i < 8; ++i) {
		p[i] = (unsigned char)(v >> (8u * i));
	}
}

static unsigned char *
slurp(const char *path, size_t *len)
{
	FILE *f = fopen(path, "rb");
	long n;
	unsigned char *buf;

	if (f == NULL) {
		fprintf(stderr, "cannot open %s\n", path);
		return NULL;
	}
	if (fseek(f, 0, SEEK_END) != 0 || (n = ftell(f)) < 0 ||
	    fseek(f, 0, SEEK_SET) != 0) {
		fclose(f);
		return NULL;
	}
	buf = malloc((size_t)n + 1);
	if (buf == NULL || fread(buf, 1, (size_t)n, f) != (size_t)n) {
		free(buf);
		fclose(f);
		return NULL;
	}
	fclose(f);
	*len = (size_t)n;
	return buf;
}

static int
sha_selftest(void)
{
	static const unsigned char abc[32] = {
		0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea,
		0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
		0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
		0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad
	};
	SHA256_CTX ctx;
	unsigned char out[32];

	SHA256_Init(&ctx);
	SHA256_Update(&ctx, "abc", 3);
	SHA256_Final(out, &ctx);
	if (memcmp(out, abc, sizeof(abc)) != 0) {
		fprintf(stderr, "host SHA-256 does not match NIST \"abc\"\n");
		return -1;
	}
	return 0;
}

static void
build_adt(unsigned int entries, uint64_t dram_size, int size_delta)
{
	unsigned char dram[8];
	unsigned char pair[16];
	DTEntry chosen, mmap;
	unsigned int i;

	kadt_reset();
	put_le64(dram, DRAM_BASE);
	chosen = kadt_node("/chosen");
	kadt_prop(chosen, "dram-base", dram, 8);
	put_le64(dram, dram_size);
	kadt_prop(chosen, "dram-size", dram, 8);

	mmap = kadt_node("/chosen/memory-map");
	for (i = 0; i < entries; ++i) {
		put_le64(pair, g_pa[i]);
		put_le64(pair + 8, g_adt_size[i] + (i == 0 ? (uint64_t)size_delta : 0));
		kadt_prop(mmap, fixtures[i].map_name, pair, 16);
		kmap_add(g_pa[i], g_data[i]);
	}
}

/* Returns the log, having called the gate once. */
static const char *
run_gate(int hashed)
{
	klog_reset();
	kset_bootarg("polarisgpu", 1);
	kset_bootarg("polarisgpuhash", hashed);
	polaris_gpu_init();
	return klog_text();
}

static void
expect(const char *scenario, const char *log, const char *want, int present)
{
	if ((strstr(log, want) != NULL) == (present != 0)) {
		return;
	}
	printf("FAIL %s: \"%s\" %s, log was:\n%s\n", scenario, want,
	    present ? "missing" : "unexpectedly present", log);
	g_failures++;
}

int
main(int argc, char **argv)
{
	const char *dir;
	unsigned int i;
	const char *log;

	if (argc < 2) {
		fprintf(stderr, "usage: gate <firmware-dir>\n");
		return 2;
	}
	dir = argv[1];
	if (sha_selftest() != 0) {
		return 2;
	}
	for (i = 0; i < BLOBS; ++i) {
		char path[1024];

		snprintf(path, sizeof(path), "%s/%s", dir, fixtures[i].file);
		g_data[i] = slurp(path, &g_size[i]);
		if (g_data[i] == NULL) {
			return 2;
		}
		g_pa[i] = fixtures[i].pa;
		g_adt_size[i] = g_size[i];
	}

	/* 1. The real set, hashed: layout and digests must both pass. */
	build_adt(BLOBS, DRAM_SIZE, 0);
	log = run_gate(1);
	expect("real set", log, "layout accepted, 6 blobs", 1);
	expect("real set", log, "firmware proven against the pinned set", 1);
	expect("real set", log, "firmware backend not implemented", 1);
	expect("real set", log, "acceleration unavailable", 1);

	/* 2. Not hashed: layout passes, firmware stays unproven. */
	build_adt(BLOBS, DRAM_SIZE, 0);
	log = run_gate(0);
	expect("unhashed", log, "layout accepted, 6 blobs", 1);
	expect("unhashed", log, "firmware unhashed", 1);
	expect("unhashed", log, "firmware proven against", 0);

	/* 3. One byte changed in the SQE: the digest must catch it. */
	build_adt(BLOBS, DRAM_SIZE, 0);
	g_data[0][100] ^= 0xffu;
	log = run_gate(1);
	expect("corrupt sqe", log, "firmware not proven", 1);
	expect("corrupt sqe", log, "mismatch", 1);
	expect("corrupt sqe", log, "firmware proven against", 0);
	g_data[0][100] ^= 0xffu;

	/* 4. A missing memory-map entry is a missing blob, never a default. */
	build_adt(BLOBS - 1, DRAM_SIZE, 0);
	log = run_gate(1);
	expect("missing entry", log, "ADT has no GPU-ZAP-B02 entry", 1);
	expect("missing entry", log, "layout accepted", 0);

	/* 5. A size the set does not pin stops before any hashing. */
	build_adt(BLOBS, DRAM_SIZE, 4);
	log = run_gate(1);
	expect("wrong size", log, "handoff rejected", 1);
	expect("wrong size", log, "size", 1);
	expect("wrong size", log, "layout accepted", 0);

	/* 6. DRAM that does not cover the blobs is rejected, not clamped. */
	build_adt(BLOBS, 0x01000000ull, 0);	/* 16 MiB: below the blobs */
	log = run_gate(1);
	expect("short dram", log, "handoff rejected", 1);
	expect("short dram", log, "outside", 1);
	expect("short dram", log, "layout accepted", 0);

	/* 7. The board predicate is the outermost gate. */
	build_adt(BLOBS, DRAM_SIZE, 0);
	klog_reset();
	kset_board(false);
	kset_bootarg("polarisgpu", 1);
	kset_bootarg("polarisgpuhash", 1);
	polaris_gpu_init();
	kset_board(true);
	expect("other board", klog_text(), "POLARIS GPU", 0);

	if (g_failures != 0) {
		printf("%u scenario check(s) failed\n", g_failures);
		return 1;
	}
	printf("gate: 7 scenarios behave as specified (real set proven, "
	    "corrupt/missing/short refused)\n");
	return 0;
}
