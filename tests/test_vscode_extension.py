"""VSCode 插件的静态与加载层验证。

**这里没有真实 VSCode。** 本机没有可自动化的 VSCode 实例，所以测的是能测的
那一层：清单合法、命令声明与实现一致、语法正确、用 stub 加载后 `activate()`
不崩且把该注册的都注册了。

「清单里写了命令但代码里没注册」是最容易悄悄溜过去的错——用户在命令面板里
看到一条命令，点下去说找不到实现。所以这条守卫是双向的。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "editors" / "vscode"
PACKAGE_JSON = EXT_DIR / "package.json"
EXTENSION_JS = EXT_DIR / "extension.js"

_NODE = shutil.which("node")
needs_node = pytest.mark.skipif(_NODE is None, reason="本机没有 node")


# --- 清单 -------------------------------------------------------------------


def test_package_json_is_valid_and_complete():
    payload = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    assert payload["name"] == "heavyheader"
    assert payload["main"] == "./extension.js"
    assert payload["engines"]["vscode"]
    # 没有 activationEvents 插件根本不会被加载，而这在打包时不会报错。
    assert payload["activationEvents"]
    assert payload["contributes"]["commands"]


def test_declared_commands_are_all_implemented():
    """声明了却没注册：用户在命令面板里点得到、执行不了。"""
    payload = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    source = EXTENSION_JS.read_text(encoding="utf-8")

    declared = [c["command"] for c in payload["contributes"]["commands"]]
    assert declared
    for command in declared:
        assert f"registerCommand('{command}'" in source, (
            f"package.json 声明了 {command}，extension.js 里却没有注册它"
        )


def test_registered_commands_are_all_declared():
    """反向也查：注册了却没声明，命令能跑但没人找得到（也逃过市场校验）。"""
    payload = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    source = EXTENSION_JS.read_text(encoding="utf-8")

    declared = {c["command"] for c in payload["contributes"]["commands"]}
    registered = set()
    marker = "registerCommand('"
    index = 0
    while True:
        index = source.find(marker, index)
        if index < 0:
            break
        end = source.find("'", index + len(marker))
        registered.add(source[index + len(marker) : end])
        index = end

    assert registered, "没找到任何 registerCommand，插件形同没有功能"
    assert registered <= declared, f"注册了未声明的命令：{registered - declared}"


def test_no_seconds_are_advertised():
    """CodeLens 只说行数，不说秒。

    本工具测的是解析行数，不是墙钟时间；两者不能换算。把「约省 N 行解析量」
    写成「省 N 秒」，读者会以为那是计时结果——这是措辞纪律，不是风格问题。
    """
    source = EXTENSION_JS.read_text(encoding="utf-8")
    assert "行解析量" in source
    # 扫描本身的用时可以报（那是真计时的），但候选成本不许换算成秒——
    # 所以只检查 describe() 这一段，而不是全文件。
    start = source.index("function describe(")
    body = source[start : source.index("\n}", start)]
    assert "秒" not in body, body


# --- 加载（需要 node） ------------------------------------------------------

#: 用 stub 顶掉 vscode 模块，真跑一次 activate。
_DRIVER = r"""
const Module = require('module');
const path = require('path');

const registered = [];
const providers = [];
const messages = { info: [], error: [] };

class Position { constructor(line, character) { this.line = line; this.character = character; } }
class Range { constructor(start, end) { this.start = start; this.end = end; } }
class CodeLens { constructor(range, command) { this.range = range; this.command = command; } }

const vscodeStub = {
  StatusBarAlignment: { Left: 1, Right: 2 },
  window: {
    createOutputChannel: () => ({ appendLine() {}, show() {}, dispose() {} }),
    createStatusBarItem: () => ({ text: '', show() {}, hide() {}, dispose() {} }),
    showErrorMessage: (m) => { messages.error.push(String(m)); },
    showInformationMessage: (m) => { messages.info.push(String(m)); },
  },
  workspace: {
    getConfiguration: () => ({ get: (_key, fallback) => fallback }),
    workspaceFolders: undefined,
  },
  commands: {
    registerCommand: (id, fn) => { registered.push(id); return { dispose() {} }; },
  },
  languages: {
    registerCodeLensProvider: (selector, provider) => {
      providers.push({ selector, provider });
      return { dispose() {} };
    },
  },
  CodeLens, Range, Position,
};

const originalLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'vscode') { return vscodeStub; }
  return originalLoad.call(this, request, ...rest);
};

const ext = require(__EXTENSION__);
const context = { subscriptions: [] };
ext.activate(context);

// 构造一份扫描产物，验证按编译单元归拢与 CodeLens 位置。
const root = path.resolve(__ROOT__);
const payload = {
  candidates: [
    { tu: 'app/main.cpp', header: 'include/lonely.h', line: 5,
      estimated_lines_saved: 22316, usage_label: '疑似未使用' },
    { tu: 'app/main.cpp', header: 'include/core.h', line: 1,
      estimated_lines_saved: 6, usage_label: '正在使用' },
    { tu: 'app/util.cpp', header: 'include/core.h', line: 1,
      estimated_lines_saved: 66020, usage_label: '正在使用' },
    { tu: 'app/main.cpp', header: 'include/noline.h', line: null,
      estimated_lines_saved: 10, usage_label: '正在使用' },
  ],
};
const byUnit = ext.collect(payload, root);
// 装进插件缓存，这样 provider 才是被测到的那个真东西。
const unitsLoaded = ext.loadScan(payload, root);

const document = {
  uri: { fsPath: path.join(root, 'app', 'main.cpp') },
  lineCount: 10,
  lineAt: (i) => ({ range: new Range(new Position(i, 0), new Position(i, 0)) }),
};
const lenses = providers[0].provider.provideCodeLenses(document);

console.log(JSON.stringify({
  registered,
  providerCount: providers.length,
  subscriptions: context.subscriptions.length,
  units: [...byUnit.keys()].map((k) => k),
  unitsLoaded,
  // 用与插件内部一致的规则算 key：Windows 上小写，其余原样。
  mainHits: byUnit.get(
    (process.platform === 'win32'
      ? path.resolve(root, 'app', 'main.cpp').toLowerCase()
      : path.resolve(root, 'app', 'main.cpp'))
  ) || [],
  lensTitles: lenses.map((l) => l.command.title),
  lensLines: lenses.map((l) => l.range.start.line),
  lensCommands: lenses.map((l) => l.command.command),
}));
"""


def _run_driver(tmp_path: Path, root: str) -> dict:
    driver = tmp_path / "driver.js"
    # 两个占位符都必须换成**带引号的 JS 字符串字面量**：Windows 路径里的
    # 反斜杠不做 JSON 转义会在 node 里变成语法错误。
    driver.write_text(
        _DRIVER.replace("__EXTENSION__", json.dumps(str(EXTENSION_JS))).replace(
            "__ROOT__", json.dumps(root)
        ),
        encoding="utf-8",
    )
    done = subprocess.run(
        [_NODE, str(driver)],
        capture_output=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


@needs_node
def test_extension_syntax_is_valid():
    done = subprocess.run(
        [_NODE, "--check", str(EXTENSION_JS)],
        capture_output=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stdout + done.stderr


@needs_node
def test_activate_registers_commands_and_provider(tmp_path: Path):
    payload = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    declared = {c["command"] for c in payload["contributes"]["commands"]}

    result = _run_driver(tmp_path, str(tmp_path / "proj"))

    assert set(result["registered"]) == declared
    assert result["providerCount"] == 1
    assert result["subscriptions"] >= 5


@needs_node
def test_candidates_are_grouped_by_unit_and_skipped_without_line(tmp_path: Path):
    """没有行号的候选不进 CodeLens：位置不明的提示比不提示更误导。"""
    result = _run_driver(tmp_path, str(tmp_path / "proj"))

    assert len(result["mainHits"]) == 2, result["mainHits"]
    # util.cpp 的候选不该出现在 main.cpp 上——按单元归拢错了就是张冠李戴。
    assert all("core.h" not in h["header"] or h["linesSaved"] == 6
               for h in result["mainHits"]), result["mainHits"]


@needs_node
def test_code_lens_uses_zero_based_lines(tmp_path: Path):
    """扫描给 1 起行号，VSCode 要 0 起；差 1 会让提示挂到上一行。"""
    result = _run_driver(tmp_path, str(tmp_path / "proj"))

    assert result["lensLines"] == [4, 0], result["lensLines"]
    assert all(c == "heavyheader.showDetails" for c in result["lensCommands"])
    assert all("行解析量" in t for t in result["lensTitles"]), result["lensTitles"]
