#include "core.h"
#include "unguarded.h"
int util() { Core c; return c.v.size() + unguarded(); }
