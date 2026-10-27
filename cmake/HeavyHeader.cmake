# HeavyHeader —— C++ 头文件构建成本归因与门禁（CMake 集成）
#
# 引入：
#
#   set(CMAKE_EXPORT_COMPILE_COMMANDS ON)          # 必需：扫描靠它拿到真实的编译命令
#   list(APPEND CMAKE_MODULE_PATH "<heavyheader 仓库>/cmake")
#   include(HeavyHeader)
#
# 用法：
#
#   heavyheader_scan(myapp)                        # 产出 <构建目录>/heavyheader-myapp.json
#   heavyheader_gate(myapp BASELINE baseline.json) # 相对基线回归超阈值则失败
#
# 两个目标**默认不挂进 ALL**。扫描会把每个编译单元重新编译一遍（这就是它测量
# 成本的方式），挂进默认构建等于让每个开发者的每次编译翻一倍——代价由所有人
# 承担，收益只给关心构建成本的人。要挂进去请显式给 ``ALL``。
#
# 调用方式可用 ``HEAVYHEADER_COMMAND`` 覆盖（例如指向某个虚拟环境里的解释器）。

include_guard(GLOBAL)

if(NOT HEAVYHEADER_COMMAND)
  find_package(Python3 COMPONENTS Interpreter QUIET)
  if(Python3_Interpreter_FOUND)
    set(HEAVYHEADER_COMMAND "${Python3_EXECUTABLE}" -m heavyheader)
  else()
    # 作为命令行工具装在系统上时（pip install heavyheader）直接用可执行文件名。
    set(HEAVYHEADER_COMMAND heavyheader)
  endif()
endif()

set(HEAVYHEADER_COMMAND
    "${HEAVYHEADER_COMMAND}"
    CACHE STRING "调用 heavyheader 的命令"
)

function(_heavyheader_compile_commands out_var)
  if(NOT CMAKE_EXPORT_COMPILE_COMMANDS)
    message(
      WARNING
      "HeavyHeader：CMAKE_EXPORT_COMPILE_COMMANDS 未打开。"
      "没有 compile_commands.json，扫描只能靠命令行猜编译选项，"
      "失败的单元会成片出现——那时报告里每个数字都「正常」，"
      "但它描述的根本不是这个项目。请在引入 HeavyHeader 前打开它。"
    )
  endif()
  set(${out_var} "${CMAKE_BINARY_DIR}/compile_commands.json" PARENT_SCOPE)
endfunction()

function(heavyheader_scan target)
  cmake_parse_arguments(HH "ALL" "OUTPUT;JOBS;MAX_TUS" "" ${ARGN})
  if(HH_UNPARSED_ARGUMENTS)
    message(FATAL_ERROR "heavyheader_scan(${target}): 无法识别的参数：${HH_UNPARSED_ARGUMENTS}")
  endif()

  _heavyheader_compile_commands(_compile_commands)

  if(NOT HH_OUTPUT)
    set(HH_OUTPUT "${CMAKE_BINARY_DIR}/heavyheader-${target}.json")
  endif()

  set(_args scan "${CMAKE_SOURCE_DIR}" --compile-commands "${_compile_commands}"
           --format json -o "${HH_OUTPUT}")
  if(HH_JOBS)
    list(APPEND _args -j "${HH_JOBS}")
  endif()
  if(HH_MAX_TUS)
    list(APPEND _args --max-tus "${HH_MAX_TUS}")
  endif()

  set(_all "")
  if(HH_ALL)
    set(_all ALL)
  endif()

  add_custom_target(
    heavyheader-scan-${target} ${_all}
    COMMAND ${HEAVYHEADER_COMMAND} ${_args}
    DEPENDS "${_compile_commands}"
    BYPRODUCTS "${HH_OUTPUT}"
    WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
    COMMENT "HeavyHeader：扫描 ${target} 的头文件构建成本"
    VERBATIM
  )
endfunction()

function(heavyheader_gate target)
  cmake_parse_arguments(HH "ALL;UPDATE_BASELINE" "BASELINE;MAX_REGRESSION;JOBS;MAX_TUS" "" ${ARGN})
  if(HH_UNPARSED_ARGUMENTS)
    message(FATAL_ERROR "heavyheader_gate(${target}): 无法识别的参数：${HH_UNPARSED_ARGUMENTS}")
  endif()
  if(NOT HH_BASELINE)
    message(FATAL_ERROR "heavyheader_gate(${target}): 必须给 BASELINE ——门禁比的是「相对什么回归」")
  endif()

  _heavyheader_compile_commands(_compile_commands)

  set(_args gate "${CMAKE_SOURCE_DIR}" --compile-commands "${_compile_commands}"
           --baseline "${HH_BASELINE}")
  if(HH_MAX_REGRESSION)
    list(APPEND _args --max-regression "${HH_MAX_REGRESSION}")
  endif()
  if(HH_UPDATE_BASELINE)
    list(APPEND _args --update-baseline)
  endif()
  if(HH_JOBS)
    list(APPEND _args -j "${HH_JOBS}")
  endif()
  if(HH_MAX_TUS)
    list(APPEND _args --max-tus "${HH_MAX_TUS}")
  endif()

  set(_all "")
  if(HH_ALL)
    set(_all ALL)
  endif()

  add_custom_target(
    heavyheader-gate-${target} ${_all}
    COMMAND ${HEAVYHEADER_COMMAND} ${_args}
    DEPENDS "${_compile_commands}"
    WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
    COMMENT "HeavyHeader：头文件成本相对基线的回归门禁"
    VERBATIM
  )
endfunction()
