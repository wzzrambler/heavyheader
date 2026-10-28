// HeavyHeader —— 在编辑器里直接看到「这个 include 让你多等几秒」。
//
// 三条纪律，改动本文件前请先知道：
//
// 1. **只报行数，不报秒。** 本工具测的是头文件解析量（行数），不是墙钟时间；
//    两者不能换算（预处理占比推算实测收益会高估约 1.6 倍，见 docs/methodology.md
//    第 15 章）。所以 CodeLens 上写的是「约省 N 行解析量」，不是「省 N 秒」。
//    这句措辞是刻意的：换成秒，读者会以为那是计时结果。
//
// 2. **不自动扫描。** 全量扫描会把每个编译单元重新编译一遍——代价与一次完整
//    构建同量级。挂在保存或改动事件上等于让编辑器每隔几分钟重编一次工程。
//    所以只由用户显式触发（`heavyheader.scan` 命令）。
//
// 3. **只对候选出镜。** 不是每个 include 都值得看；只有进入成本候选的才显示，
//    与 CLI 报告的口径一致（避免编辑器里冒出一份和报告对不上的数字）。
//
// 验证状态：本文件经过「用 stub 加载并 activate」的测试（命令注册一致、
// activate 不崩），**未在真实 VSCode 里端到端跑过**。

const vscode = require('vscode');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 编译单元绝对路径 -> 该单元里的候选行。扫描一次，缓存到用户手动清除。
const results = new Map();

let output = null;
let statusBar = null;

function log(message) {
  if (output) {
    output.appendLine(message);
  }
}

/** Windows 上路径比较要忽略大小写与分隔符方向。 */
function normaliseKey(filePath) {
  const resolved = path.resolve(filePath);
  return os.platform() === 'win32' ? resolved.toLowerCase() : resolved;
}

/** 候选里的 tu 可能是相对扫描根目录的，也可能是绝对的——两种都要能定位。 */
function resolveUnit(root, tu) {
  if (!tu) {
    return null;
  }
  return path.isAbsolute(tu) ? tu : path.resolve(root, tu);
}

/**
 * 把候选按编译单元归拢。
 *
 * 只保留有行号的：CodeLens 要挂在具体一行上，行号缺失时（例如候选来自头内部
 * 的 include 点）给出的是位置不明的提示，反而误导。
 */
function collect(payload, root) {
  const byUnit = new Map();
  for (const candidate of payload.candidates || []) {
    if (typeof candidate.line !== 'number') {
      continue;
    }
    const unit = resolveUnit(root, candidate.tu);
    if (!unit) {
      continue;
    }
    const key = normaliseKey(unit);
    if (!byUnit.has(key)) {
      byUnit.set(key, []);
    }
    byUnit.get(key).push({
      line: candidate.line,
      header: candidate.header || '',
      linesSaved: candidate.estimated_lines_saved || 0,
      usage: candidate.usage_label || '',
      verdict: candidate.advice ? candidate.advice.verdict : null,
    });
  }
  return byUnit;
}

/**
 * 把一次扫描的产物装进缓存，返回涉及的编译单元数。
 *
 * 与「跑进程」分开：这样这份解析逻辑可以单独被验证，不必为了测它而启动一次
 * 全量扫描（那是与一次完整构建同量级的操作）。
 */
function loadScan(payload, root) {
  results.clear();
  for (const [key, hits] of collect(payload, root)) {
    results.set(key, hits);
  }
  return results.size;
}

/** CodeLens 文案。只说行数与判定，不换算时间。 */
function describe(hit) {
  const saved = hit.linesSaved.toLocaleString('en-US');
  const parts = [`约省 ${saved} 行解析量`];
  if (hit.verdict === 'remove') {
    parts.push('实测可删');
  } else if (hit.verdict === 'keep') {
    parts.push('实测删不得');
  } else if (hit.usage) {
    parts.push(hit.usage);
  }
  return parts.join(' · ');
}

function runScan() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showErrorMessage('HeavyHeader：请先打开一个工程目录。');
    return;
  }
  const root = folders[0].uri.fsPath;
  const config = vscode.workspace.getConfiguration('heavyheader');
  const command = config.get('command', 'heavyheader');
  const extraArgs = config.get('extraArgs', []);

  const outFile = path.join(
    os.tmpdir(),
    `heavyheader-scan-${process.pid}-${Date.now()}.json`
  );
  const args = ['scan', root, '--format', 'json', '-o', outFile].concat(extraArgs);

  if (output) {
    output.show(true);
  }
  log(`HeavyHeader：扫描 ${root}`);
  log(`$ ${command} ${args.join(' ')}`);

  const started = Date.now();
  const child = spawn(command, args, { cwd: root });

  let stderr = '';
  child.stdout.on('data', (chunk) => log(String(chunk)));
  child.stderr.on('data', (chunk) => {
    stderr += String(chunk);
  });

  child.on('error', (error) => {
    vscode.window.showErrorMessage(
      `HeavyHeader：调不起来 ${command}（${error.message}）。` +
        '请确认已 pip install heavyheader，或在设置里指定 heavyheader.command。'
    );
  });

  child.on('close', (code) => {
    // 退出码非 0 时也要看产物：scan 在部分单元编译失败时仍会写出 JSON，
    // 报告正文会写明失败占比。这里把 stderr 留给输出面板，不替用户判断。
    log(stderr);
    if (code !== 0 && !fs.existsSync(outFile)) {
      vscode.window.showErrorMessage(
        `HeavyHeader：扫描失败（退出码 ${code}），详见「HeavyHeader」输出面板。`
      );
      return;
    }
    if (!fs.existsSync(outFile)) {
      vscode.window.showErrorMessage('HeavyHeader：扫描没有产出 JSON。');
      return;
    }

    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(outFile, 'utf8'));
    } catch (error) {
      vscode.window.showErrorMessage(`HeavyHeader：扫描结果不是合法 JSON（${error.message}）。`);
      return;
    } finally {
      fs.unlink(outFile, () => {});
    }

    loadScan(payload, root);
    const total = [...results.values()].reduce((sum, hits) => sum + hits.length, 0);
    const seconds = ((Date.now() - started) / 1000).toFixed(1);
    log(`HeavyHeader：${results.size} 个编译单元、${total} 条候选，用时 ${seconds}s`);
    updateStatusBar(total);
    vscode.window.showInformationMessage(
      `HeavyHeader：扫出 ${total} 条成本候选（${results.size} 个编译单元，用时 ${seconds}s）。`
    );
  });
}

function updateStatusBar(total) {
  if (!statusBar) {
    return;
  }
  statusBar.text = `HeavyHeader: ${total} 条候选`;
  statusBar.show();
}

function clearResults() {
  results.clear();
  updateStatusBar(0);
  vscode.window.showInformationMessage('HeavyHeader：已清除扫描结果。');
}

function showDetails(hit) {
  if (!hit) {
    return;
  }
  vscode.window.showInformationMessage(
    `${path.basename(hit.header)} — ${describe(hit)}`
  );
}

function activate(context) {
  output = vscode.window.createOutputChannel('HeavyHeader');
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  updateStatusBar(0);

  context.subscriptions.push(
    output,
    statusBar,
    vscode.commands.registerCommand('heavyheader.scan', runScan),
    vscode.commands.registerCommand('heavyheader.clear', clearResults),
    vscode.commands.registerCommand('heavyheader.showDetails', showDetails),
    vscode.languages.registerCodeLensProvider(
      [{ language: 'cpp' }, { language: 'c' }],
      {
        provideCodeLenses(document) {
          const hits = results.get(normaliseKey(document.uri.fsPath)) || [];
          return hits.map((hit) => {
            // 扫描给的是 1 起的行号，VSCode 用 0 起；越界就退到首行，
            // 免得文件被改动后 provider 抛异常把整个 CodeLens 面板搞坏。
            let index = Math.max(0, hit.line - 1);
            if (index >= document.lineCount) {
              index = 0;
            }
            return new vscode.CodeLens(document.lineAt(index).range, {
              title: `HeavyHeader：${describe(hit)}`,
              command: 'heavyheader.showDetails',
              arguments: [hit],
            });
          });
        },
      }
    )
  );
}

function deactivate() {
  results.clear();
}

module.exports = { activate, deactivate, collect, describe, loadScan };
