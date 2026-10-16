#include "core.h"
#include "heavy.h"
#include "mid.h"
#include "unguarded.h"
#include "lonely.h"
int main() { Core c; Heavy h; return c.v.size() + h.s.size() + mid(); }
