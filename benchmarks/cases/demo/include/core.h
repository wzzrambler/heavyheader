#pragma once
// 被两个编译单元引用：成本热点
#include <vector>
#include <map>
#include <string>
struct Core { std::vector<int> v; std::map<std::string, int> m; };
