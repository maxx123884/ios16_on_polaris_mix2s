/*
 * Host unit test for the Polaris GPU handoff validator (bsd/dev/
 * polaris_gpu_validate.c). Runs on the build host; reads no hardware.
 *
 *   cc -std=c99 -Wall -Wextra -I../bsd/dev test_polaris_gpu_validate.c \
 *      ../bsd/dev/polaris_gpu_validate.c -o test_polaris_gpu_validate
 *   ./test_polaris_gpu_validate
 *
 * Every case asserts a refusal path that must exist BEFORE any backend is
 * allowed to vote a rail, map SMMU memory or start firmware.
 */
#include <stdio.h>
#include <string.h>

#include "../bsd/dev/polaris_gpu_validate.h"

static unsigned int failures;
static enum pg_gx_action action;

#define CHECK(cond) do {						\
	if (!(cond)) {							\
		printf("FAIL %d: %s\n", __LINE__, #cond);		\
		failures++;						\
	}								\
} while (0)

#define EXPECT_STATUS(expr, want) do {					\
	enum pg_status got_ = (expr);					\
	if (got_ != (want)) {						\
		printf("FAIL %d: %s -> %s, want %s\n", __LINE__, #expr,	\
		    pg_status_name(got_), pg_status_name(want));	\
		failures++;						\
	}								\
} while (0)

/*
 * Layout mirrors the real handoff: XNU manages the upper DDR bank at
 * 0x100000000, the zap carveout and ABL splash sit outside it, and the six
 * firmware blobs are packed page-aligned from +1 MiB.
 */
#define DRAM_BASE	0x100000000ull
#define DRAM_SIZE	0x7cc00000ull
#define ZAP_BASE	0x8c415000ull
#define ZAP_SIZE	0x2000ull
#define FB_BASE		0x9d400000ull
#define FB_SIZE		0x2400000ull
#define BLOB_ORIGIN	(DRAM_BASE + 0x100000ull)

static const uint64_t device_bytes[PG_BLOB_COUNT] = {
	32136, 32768, 6684, 148, 6536, 1968
};

static void
fixture(struct pg_handoff *handoff, struct pg_expect *expect)
{
	uint64_t at = BLOB_ORIGIN;
	unsigned int i, j;

	memset(handoff, 0, sizeof(*handoff));
	memset(expect, 0, sizeof(struct pg_expect) * PG_BLOB_COUNT);
	handoff->adt_polaris = true;
	handoff->adt_sdm845 = true;
	handoff->dram.base = DRAM_BASE;
	handoff->dram.size = DRAM_SIZE;
	handoff->zap.base = ZAP_BASE;
	handoff->zap.size = ZAP_SIZE;
	handoff->splash.base = FB_BASE;
	handoff->splash.size = FB_SIZE;
	for (i = 0; i < PG_BLOB_COUNT; ++i) {
		uint64_t reserve = (device_bytes[i] + PG_PAGE - 1) &
		    ~(PG_PAGE - 1);
		handoff->blob[i].pa = at;
		handoff->blob[i].bytes = device_bytes[i];
		expect[i].bytes = device_bytes[i];
		for (j = 0; j < PG_HASH_BYTES; ++j) {
			/* Deterministic per-blob digest, not all zero. */
			handoff->blob[i].sha256[j] = (uint8_t)(i * 37 + j + 1);
			expect[i].sha256[j] = handoff->blob[i].sha256[j];
		}
		at += reserve;
	}
}

int
main(void)
{
	struct pg_handoff handoff;
	struct pg_expect expect[PG_BLOB_COUNT];
	struct pg_validate_result result;
	enum pg_status status;

	/* 1. The valid handoff is accepted and reports the page-rounded plan. */
	fixture(&handoff, expect);
	memset(&result, 0, sizeof(result));
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_OK);
	CHECK(result.blobs_ok == PG_BLOB_COUNT);
	CHECK(result.mapped_bytes == 0x20000ull);

	/* 2. Absent arguments never default to success. */
	EXPECT_STATUS(pg_validate_handoff(NULL, expect, &result), PG_EINVAL);
	EXPECT_STATUS(pg_validate_handoff(&handoff, NULL, &result), PG_EINVAL);
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, NULL), PG_EINVAL);

	/* 3. Both ADT identities are required. */
	fixture(&handoff, expect);
	handoff.adt_polaris = false;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBOARD);
	fixture(&handoff, expect);
	handoff.adt_sdm845 = false;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBOARD);

	/* 4. Implausible managed DRAM. */
	fixture(&handoff, expect);
	handoff.dram.size = 0;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);
	fixture(&handoff, expect);
	handoff.dram.base = UINT64_MAX - 8;
	handoff.dram.size = 16;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);
	fixture(&handoff, expect);
	handoff.dram.base = DRAM_BASE + 1;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);
	fixture(&handoff, expect);
	handoff.dram.size = PG_MAX_DRAM + PG_PAGE;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);

	/* 5. Reserved memory must be described and must stay out of DRAM. */
	fixture(&handoff, expect);
	handoff.zap.size = 0;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);
	fixture(&handoff, expect);
	handoff.zap.base = DRAM_BASE + 0x200000;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_ERESERVED);
	fixture(&handoff, expect);
	handoff.splash.base = DRAM_BASE + DRAM_SIZE - FB_SIZE;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_ERESERVED);

	/* 6. A blob that is not the device-observed size is refused. */
	fixture(&handoff, expect);
	handoff.blob[1].bytes = 32767;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_SIZE);
	fixture(&handoff, expect);
	handoff.blob[4].bytes = 0;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_SIZE);

	/* 7. Alignment and containment. */
	fixture(&handoff, expect);
	handoff.blob[2].pa += 1;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_ALIGN);
	fixture(&handoff, expect);
	handoff.blob[0].pa = DRAM_BASE - PG_PAGE;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_OUTSIDE);
	/*
	 * A page-aligned blob can never straddle a page-aligned DRAM end, so the
	 * remaining containment cases are "starts at the end" and "far above it".
	 * The latter also proves the bounds arithmetic does not wrap.
	 */
	fixture(&handoff, expect);
	handoff.blob[5].pa = DRAM_BASE + DRAM_SIZE;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_OUTSIDE);
	fixture(&handoff, expect);
	handoff.blob[5].pa = UINT64_MAX - 0x7fffull;	/* aligned, no wraparound */
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_OUTSIDE);

	/* 8. Two blobs may never share a page. */
	fixture(&handoff, expect);
	handoff.blob[3].pa = handoff.blob[2].pa;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_OVERLAP);

	/* 9. Unproven digests are a refusal, not a warning. */
	fixture(&handoff, expect);
	memset(expect[2].sha256, 0, PG_HASH_BYTES);
	CHECK(!pg_expect_complete(expect, PG_BLOB_COUNT));
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EHASH_UNPROVEN);
	fixture(&handoff, expect);
	expect[0].bytes = 0;
	CHECK(!pg_expect_complete(expect, PG_BLOB_COUNT));
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EHASH_UNPROVEN);
	CHECK(!pg_expect_complete(NULL, PG_BLOB_COUNT));
	CHECK(!pg_expect_complete(expect, PG_BLOB_COUNT - 1));

	/*
	 * 9b. A filled table does not turn unhashed bytes into "wrong bytes".
	 * This is the state of the driver in this revision, so the status it
	 * prints must point at the missing digest backend, not at the firmware.
	 */
	fixture(&handoff, expect);
	CHECK(pg_expect_complete(expect, PG_BLOB_COUNT));
	memset(handoff.blob[1].sha256, 0, PG_HASH_BYTES);
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EHASH_UNPROVEN);

	/*
	 * 9c. The two phases are usable on their own. That is what lets the
	 * driver prove the layout while it still has no way to hash memory, and
	 * what keeps "layout wrong" and "bytes unhashed" from collapsing into
	 * one status.
	 */
	fixture(&handoff, expect);
	memset(handoff.blob[0].sha256, 0, PG_HASH_BYTES);
	EXPECT_STATUS(pg_validate_layout(&handoff, expect, &result), PG_OK);
	CHECK(result.blobs_ok == PG_BLOB_COUNT);
	EXPECT_STATUS(pg_validate_digests(&handoff, expect), PG_EHASH_UNPROVEN);
	fixture(&handoff, expect);
	handoff.blob[3].sha256[0] ^= 0xff;
	EXPECT_STATUS(pg_validate_layout(&handoff, expect, &result), PG_OK);
	EXPECT_STATUS(pg_validate_digests(&handoff, expect), PG_EHASH_MISMATCH);
	fixture(&handoff, expect);
	memset(expect[0].sha256, 0, PG_HASH_BYTES);
	EXPECT_STATUS(pg_validate_layout(&handoff, expect, &result), PG_EHASH_UNPROVEN);
	EXPECT_STATUS(pg_validate_digests(&handoff, expect), PG_EHASH_UNPROVEN);
	fixture(&handoff, expect);
	EXPECT_STATUS(pg_validate_layout(NULL, expect, &result), PG_EINVAL);
	EXPECT_STATUS(pg_validate_layout(&handoff, NULL, &result), PG_EINVAL);
	EXPECT_STATUS(pg_validate_layout(&handoff, expect, NULL), PG_EINVAL);
	EXPECT_STATUS(pg_validate_digests(NULL, expect), PG_EINVAL);
	EXPECT_STATUS(pg_validate_digests(&handoff, NULL), PG_EINVAL);

	/* 10. A wrong digest is refused even when every range is perfect. */
	fixture(&handoff, expect);
	handoff.blob[3].sha256[7] ^= 0x01;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EHASH_MISMATCH);

	/* 11. The result struct is only written on success. */
	fixture(&handoff, expect);
	result.blobs_ok = 99;
	result.mapped_bytes = 99;
	handoff.dram.size = 0;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EDRAM);
	CHECK(result.blobs_ok == 99 && result.mapped_bytes == 99);

	/*
	 * 12. A reviewed set whose SQE is an earlier revision than the one this
	 * port was measured on is accepted by its own pinned size -- and only
	 * that size. This is MIUI 12.5.1: 31980 bytes, not 32136.
	 */
	fixture(&handoff, expect);
	handoff.blob[0].bytes = 31980;
	expect[0].bytes = 31980;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_OK);
	CHECK(result.blobs_ok == PG_BLOB_COUNT);
	CHECK(result.mapped_bytes == 0x20000ull);
	fixture(&handoff, expect);
	handoff.blob[0].bytes = 31980;
	EXPECT_STATUS(pg_validate_handoff(&handoff, expect, &result), PG_EBLOB_SIZE);

	/* 13. Every status has a name. */
	for (status = PG_OK; status <= PG_EMAP; ++status) {
		CHECK(pg_status_name(status) != NULL);
	}

	/*
	 * 14. The GX decision. These are the phone's measured values from
	 * 2026-11-23 (m0a-gx-readonly-probe section 11): off, idle, vendor PDC
	 * program present, digests proven - the one state that may write REQ.
	 */
	struct pg_gx_state gx;

	memset(&gx, 0, sizeof(gx));
	gx.firmware_proven = true;
	gx.seq_start = PG_GX_PDC_SEQ_START;
	CHECK(pg_gx_decide(&gx) == PG_GX_WRITE_REQ);

	gx.firmware_proven = false;
	CHECK(pg_gx_decide(&gx) == PG_GX_REFUSE_UNPROVEN);

	gx.firmware_proven = true;
	gx.seq_busy = 1;
	CHECK(pg_gx_decide(&gx) == PG_GX_REFUSE_SEQ_BUSY);
	gx.seq_busy = 0;

	gx.control_ack = PG_GX_BIT_ON;
	gx.control_req = PG_GX_BIT_ON;
	CHECK(pg_gx_decide(&gx) == PG_GX_REFUSE_ALREADY_ON);

	gx.control_ack = 0;
	CHECK(pg_gx_decide(&gx) == PG_GX_REFUSE_INCONSISTENT);

	gx.control_req = 0;
	gx.seq_start = 0;
	CHECK(pg_gx_decide(&gx) == PG_GX_REFUSE_NO_PDC);

	CHECK(pg_gx_decide(NULL) == PG_GX_REFUSE_UNPROVEN);
	for (action = PG_GX_WRITE_REQ; action <= PG_GX_REFUSE_NO_PDC; ++action) {
		CHECK(pg_gx_action_name(action) != NULL);
	}

	if (failures != 0) {
		printf("%u check(s) failed\n", failures);
		return 1;
	}
	printf("all handoff validation cases passed\n");
	return 0;
}
