#ifndef _GATE_PEXPERT_H_
#define _GATE_PEXPERT_H_
#include <sys/param.h>
/* Same prototype as xnu-8796.141.3 pexpert/pexpert/pexpert.h. */
boolean_t PE_parse_boot_argn(const char *arg_string, void *arg_ptr, int max_arg);
#endif
