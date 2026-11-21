#ifndef _GATE_SYS_SYSTM_H_
#define _GATE_SYS_SYSTM_H_
#include <sys/param.h>

/*
 * The kernel's printf is replaced by a capture function so a host run can
 * assert on what the gate reported. Only the sources compiled with this stub
 * see the macro; libc's printf is untouched elsewhere.
 */
int kprintf(const char *fmt, ...) __printflike(1, 2);
#define printf kprintf
void klog_reset(void);
const char *klog_text(void);

#endif
