#ifndef _GATE_MACH_VM_TYPES_H_
#define _GATE_MACH_VM_TYPES_H_
/*
 * uintptr_t/size_t, not unsigned long: on Windows LLP64 a long is 32 bits and
 * would truncate the mapped pointer. XNU's vm_offset_t is a pointer-width
 * integer too, so this is also the faithful choice.
 */
#include <stddef.h>
#include <stdint.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
#endif
