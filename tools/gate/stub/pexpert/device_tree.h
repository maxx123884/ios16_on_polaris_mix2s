#ifndef _GATE_DEVICE_TREE_H_
#define _GATE_DEVICE_TREE_H_
#include <sys/param.h>

/* Signatures copied from xnu-8796.141.3 pexpert/pexpert/device_tree.h. */
struct OpaqueDTEntry;
typedef const struct OpaqueDTEntry *DTEntry;

enum { kSuccess = 1, kError = 0 };

int SecureDTLookupEntry(const DTEntry searchPoint, const char *pathName,
    DTEntry *foundEntry);
int SecureDTGetProperty(const DTEntry entry, const char *propertyName,
    const void **propertyValue, unsigned int *propertySize);
#endif
