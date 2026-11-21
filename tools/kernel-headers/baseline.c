/*
 * Baseline for tools/polaris_kernel_syntax.py: the system headers the Polaris
 * sources include, and nothing else.
 *
 * If this file does not compile, the flags or the header staging are wrong and
 * a failure on bsd/dev/polaris_gpu*.c says nothing about that code. If it does
 * compile, everything the three real sources add on top is their own doing.
 *
 * Not part of the kernel and not built by CI.
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <mach/vm_types.h>
#include <libkern/crypto/sha2.h>

int pg_baseline_probe;
