# HeavyHeader —— C++ 头文件构建成本归因与门禁（Meson 集成）
#
# 引入：把本目录放进项目（例如 vendor/heavyheader-meson），然后在 meson.build 里
#
#   heavyheader_baseline = 'baseline.json'   # 可选；只有 gate 目标需要
#   subdir('vendor/heavyheader-meson')
#
# 之后会有两个目标：
#
#   ninja -C builddir heavyheader-scan     # 扫描，产出 heavyheader-scan.json
#   ninja -C builddir heavyheader-gate     # 相对基线回归超阈值则失败
#
# 两个目标**都不是默认构建**（scan 用 build_by_default: false，gate 是
# run_target）。理由与 CMake 集成一致：扫描会把每个编译单元重新编译一遍，
# 代价不该由每一次构建承担。

# --- 1. 找到能用的 heavyheader 调用方式 ------------------------------------
#
# 两级的理由与 cmake/HeavyHeader.cmake 相同：find_program 找到的 Python
# 常常是系统 Python，而不是装了本工具的那一个。配置时看着正常，构建时才炸成
# 「No module named heavyheader」。所以第二级必须**真的 import 一次**。

heavyheader_cmd = []

heavyheader_prog = find_program('heavyheader', 'heavyheader.exe', required: false)
if heavyheader_prog.found()
  heavyheader_cmd = [heavyheader_prog]
else
  py_mod = import('python')
  py = py_mod.find_installation('python3', required: false)
  if py.found()
    check = run_command(py, '-c', 'import heavyheader', check: false)
    if check.returncode() == 0
      heavyheader_cmd = [py, '-m', 'heavyheader']
    endif
  endif
endif

if heavyheader_cmd.length() == 0
  error(
    'HeavyHeader：找不到 heavyheader。请先 pip install（会提供 console script），'
    + '或用 -Dheavyheader_command=... 之外的方式确保 python3 -m heavyheader 可用。'
    + '这里不静默退化——猜错会生成一个每次构建都失败的目标，'
    + '而错误要等到那时才炸，比配置时报错难查得多。',
  )
endif

# --- 2. compile_commands.json 的位置 ---------------------------------------
#
# 与 CMake 的差别：Meson **总是**生成 compile_commands.json，不需要打开开关。
# CMake 那边必须设 CMAKE_EXPORT_COMPILE_COMMANDS，否则扫描只能靠命令行猜编译
# 选项——猜错时不报错，只让大量单元以「找不到头文件」失败，报告每个数字都
# 「正常」，描述的却不是你的项目。
#
# 它落在**构建目录根**（不是当前子目录），并且在 meson setup 阶段就已写好，
# 所以这里不需要为它加依赖关系。

heavyheader_cc = meson.project_build_root() / 'compile_commands.json'

# --- 3. 扫描 ---------------------------------------------------------------

heavyheader_scan_out = 'heavyheader-scan.json'

custom_target(
  'heavyheader-scan',
  output: heavyheader_scan_out,
  command: heavyheader_cmd + [
    'scan', meson.project_source_root(),
    '--compile-commands', heavyheader_cc,
    '--format', 'json',
    '-o', '@OUTPUT@',
  ],
  build_by_default: false,
  console: true,
)

# --- 4. 门禁 ---------------------------------------------------------------
#
# 用 run_target 而不是 custom_target：门禁不产出任何文件，它的全部意义就是
# 「命令的退出码」。custom_target 要求声明 output，Ninja 会去检查那个文件
# 是否真的生成了——而门禁通过时什么都不该留下。

if is_variable('heavyheader_baseline')
  heavyheader_gate_args = [
    'gate', meson.project_source_root(),
    '--compile-commands', heavyheader_cc,
    '--baseline', heavyheader_baseline,
  ]
  if is_variable('heavyheader_max_regression')
    heavyheader_gate_args += ['--max-regression', heavyheader_max_regression]
  endif

  run_target(
    'heavyheader-gate',
    command: heavyheader_cmd + heavyheader_gate_args,
  )
else
  warning('HeavyHeader：未设置 heavyheader_baseline，跳过 gate 目标（scan 仍可用）')
endif
