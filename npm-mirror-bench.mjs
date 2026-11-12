#!/usr/bin/env node
/**
 * npm 镜像源测速脚本（零依赖，Node 18+）
 *
 * 测试内容：
 *   1. DNS 解析耗时
 *   2. TCP 连接耗时 / TLS 握手耗时（取首次探测）
 *   3. 延迟：多次请求包元数据，统计首字节时间（TTFB）的最快值与中位数、抖动
 *   4. 下载速度：完整下载一个固定版本的 tarball，计算 MB/s（自动跟随 302 跳转）
 *
 * 用法：
 *   node npm-mirror-bench.mjs                # 默认测 3 轮
 *   node npm-mirror-bench.mjs --runs 5       # 每源测 5 轮
 *   node npm-mirror-bench.mjs --json         # 输出 JSON（便于二次处理）
 *   node npm-mirror-bench.mjs --only 腾讯,淘宝
 *   node npm-mirror-bench.mjs --timeout 20000 --speed-timeout 30000
 *   node npm-mirror-bench.mjs --list         # 只列出内置源
 *
 * 说明：
 *   - 延迟测试并行执行；下载测速串行执行，避免互相抢带宽影响结果。
 *   - 延迟测试在收到首字节后立即断开，不浪费流量。
 *   - 源列表在下方 SOURCES 中，可自行增删。
 */

import https from 'node:https';
import http from 'node:http';
import dns from 'node:dns/promises';
import { execSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';

/* ------------------------------------------------------------------ */
/* 配置                                                                */
/* ------------------------------------------------------------------ */

/** 内置镜像源（已确认可用；失效的旧源见文件末尾注释） */
const SOURCES = [
  { name: '腾讯云', url: 'https://mirrors.cloud.tencent.com/npm/' },
  { name: '淘宝 npmmirror', url: 'https://registry.npmmirror.com' },
  { name: '华为云', url: 'https://repo.huaweicloud.com/repository/npm/' },
  { name: 'CERNET', url: 'https://mirrors.cernet.edu.cn/npm/' },
  { name: 'npm 官方', url: 'https://registry.npmjs.org' },
  { name: 'Yarn 官方', url: 'https://registry.yarnpkg.com' },
];

/** 延迟测试使用的包（元数据越小越快；react 各源都有） */
const LATENCY_PKG = 'react';

/** 下载测速使用的固定 tarball（webpack@5.97.1 约 1MB，足够反映吞吐） */
const SPEED_PKG = {
  name: 'webpack',
  version: '5.97.1',
  file: 'webpack-5.97.1.tgz',
};

/* ------------------------------------------------------------------ */
/* 参数解析                                                            */
/* ------------------------------------------------------------------ */

const argv = process.argv.slice(2);
const has = (flag) => argv.includes(flag);

function option(name, fallback) {
  const i = argv.indexOf(name);
  if (i === -1) return fallback;
  const v = argv[i + 1];
  return v === undefined || v.startsWith('--') ? fallback : v;
}

const RUNS = Math.max(1, Number(option('--runs', 3)) || 3);
const TIMEOUT = Number(option('--timeout', 12000)) || 12000;
const SPEED_TIMEOUT = Number(option('--speed-timeout', 30000)) || 30000;
const JSON_OUT = has('--json');
const VERBOSE = has('--verbose') || has('-v');
const ONLY = option('--only', '')
  .split(',')
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

const SOURCE_LIST = ONLY.length
  ? SOURCES.filter((s) => ONLY.some((k) => s.name.toLowerCase().includes(k) || s.url.toLowerCase().includes(k)))
  : SOURCES;

/* ------------------------------------------------------------------ */
/* 终端输出辅助                                                        */
/* ------------------------------------------------------------------ */

const COLOR =
  !has('--no-color') &&
  !JSON_OUT &&
  (Boolean(process.stdout.isTTY) || has('--color') || Boolean(process.env.FORCE_COLOR));

const paint = (code) => (text) => (COLOR ? `\x1b[${code}m${text}\x1b[0m` : String(text));
const green = paint('32');
const red = paint('31');
const yellow = paint('33');
const gray = paint('90');
const cyan = paint('36');
const bold = paint('1');

/** 去掉 ANSI 转义序列 */
const stripAnsi = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, '');

/** 计算显示宽度（中文/全角按 2 列） */
function displayWidth(s) {
  let w = 0;
  for (const ch of stripAnsi(s)) {
    const cp = ch.codePointAt(0);
    const wide =
      (cp >= 0x1100 && cp <= 0x115f) ||
      (cp >= 0x2e80 && cp <= 0xa4cf) ||
      (cp >= 0xac00 && cp <= 0xd7a3) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0xfe30 && cp <= 0xfe6f) ||
      (cp >= 0xff00 && cp <= 0xff60) ||
      (cp >= 0xffe0 && cp <= 0xffe6) ||
      (cp >= 0x1f300 && cp <= 0x1faff) ||
      (cp >= 0x20000 && cp <= 0x3fffd);
    w += wide ? 2 : 1;
  }
  return w;
}

const padEnd = (s, width) => String(s) + ' '.repeat(Math.max(0, width - displayWidth(s)));

/** 居中补齐（用于表头） */
function center(s, width) {
  const diff = Math.max(0, width - displayWidth(s));
  const left = Math.floor(diff / 2);
  return ' '.repeat(left) + String(s) + ' '.repeat(diff - left);
}

/* ------------------------------------------------------------------ */
/* 单次 HTTP 探测                                                      */
/* ------------------------------------------------------------------ */

/**
 * 请求一个 URL，返回计时与传输信息。
 * @returns {Promise<{ok:boolean,error?:string,status:number,tcp:number|null,
 *   tls:number|null,ttfb:number|null,bytes:number,totalMs:number}>}
 */
function probe(targetUrl, { timeout = TIMEOUT, stopAfterFirstByte = false } = {}) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    let req = null;
    let settled = false;
    let status = 0;
    let tcp = null;
    let tls = null;
    let ttfb = null;
    let bytes = 0;
    let redirects = 5;

    const done = (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (req) {
        try {
          req.destroy();
        } catch {
          /* ignore */
        }
      }
      const ok = !err && status > 0 && status < 400;
      resolve({
        ok,
        error: err ? err.code || err.message || String(err) : ok ? undefined : `HTTP ${status}`,
        status,
        tcp,
        tls,
        ttfb,
        bytes,
        totalMs: performance.now() - t0,
      });
    };

    const timer = setTimeout(() => done(new Error('timeout')), timeout);

    const open = (url) => {
      const mod = url.startsWith('https:') ? https : http;
      const current = mod.request(
        url,
        {
          method: 'GET',
          agent: false,
          headers: {
            accept: 'application/vnd.npm.install-v1+json, application/json, */*',
            'accept-encoding': 'identity',
            'user-agent': 'npm-mirror-bench/1.0 (node)',
            connection: 'close',
          },
        },
        (res) => {
          status = res.statusCode || 0;

          // 跟随 3xx 跳转（npmmirror 等会把 tarball 302 到 CDN）
          const location = res.headers.location;
          if (status >= 300 && status < 400 && location && redirects > 0) {
            redirects -= 1;
            res.resume();
            try {
              open(new URL(location, url).href);
            } catch (e) {
              done(e);
            }
            return;
          }

          res.on('data', (chunk) => {
            bytes += chunk.length;
            if (ttfb === null) {
              ttfb = performance.now() - t0;
              if (stopAfterFirstByte) {
                res.destroy();
                done(null);
              }
            }
          });
          res.on('end', () => done(null));
          res.on('error', (e) => done(e));
          res.on('aborted', () => done(null));
        },
      );

      req = current;
      current.on('socket', (socket) => {
        if (!socket.connecting) {
          tcp = performance.now() - t0;
          tls = url.startsWith('https:') ? tcp : null;
          return;
        }
        socket.once('connect', () => {
          tcp = performance.now() - t0;
        });
        socket.once('secureConnect', () => {
          tls = performance.now() - t0;
        });
      });
      current.on('error', (e) => done(e));
      current.end();
    };

    open(targetUrl);
  });
}

/* ------------------------------------------------------------------ */
/* 统计                                                                */
/* ------------------------------------------------------------------ */

const joinUrl = (base, path) => `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;

function stats(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const sum = sorted.reduce((a, b) => a + b, 0);
  const mid = Math.floor(sorted.length / 2);
  return {
    min: sorted[0],
    max: sorted[sorted.length - 1],
    avg: sum / sorted.length,
    median: sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2,
    jitter: sorted[sorted.length - 1] - sorted[0],
  };
}

const fmtMs = (v) => (v == null ? '—' : `${Math.round(v)}ms`);
const fmtSpeed = (v) => (v == null ? '—' : `${v.toFixed(2)} MB/s`);

/** 读取当前 npm registry（读取失败返回空串） */
function currentRegistry() {
  try {
    return execSync('npm config get registry', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
      .trim()
      .toLowerCase()
      .replace(/\/+$/, '');
  } catch {
    return '';
  }
}

/* ------------------------------------------------------------------ */
/* 主流程                                                              */
/* ------------------------------------------------------------------ */

if (has('--list') || has('-l')) {
  console.log(bold('\n内置源：'));
  for (const s of SOURCES) console.log(`  ${padEnd(s.name, 16)} ${s.url}`);
  console.log(gray('\n提示：腾讯云源路径带 /npm/，其余为注册表根地址。\n'));
  process.exit(0);
}

if (!SOURCE_LIST.length) {
  console.error(red('没有匹配的源，请用 --list 查看内置源名称。'));
  process.exit(1);
}

const activeRegistry = currentRegistry();

if (!JSON_OUT) {
  console.log(
    `\n${bold('npm 镜像源测速')}  ${gray(
      `每源延迟 ${RUNS} 次 · 超时 ${TIMEOUT / 1000}s · 测速包 ${SPEED_PKG.name}@${SPEED_PKG.version}`,
    )}`,
  );
  console.log(gray(`测试时间：${new Date().toLocaleString()}`));
  console.log(gray(`当前 npm registry：${activeRegistry || '未知'}`));
}

/* ---------- 阶段一：DNS + 延迟（并行） ---------- */

const results = await Promise.all(
  SOURCE_LIST.map(async (source) => {
    const base = source.url.replace(/\/+$/, '');
    const host = new URL(base).hostname;

    const result = {
      name: source.name,
      url: base,
      host,
      dns: null,
      dnsError: null,
      ip: null,
      tcp: null,
      tls: null,
      latency: null,
      speed: null,
      ok: false,
      error: null,
      current: base.toLowerCase() === activeRegistry,
    };

    // DNS
    try {
      const t = performance.now();
      const addrs = await dns.lookup(host, { all: true });
      result.dns = performance.now() - t;
      result.ip = addrs[0]?.address ?? null;
    } catch (e) {
      result.dnsError = e.code || e.message;
    }

    // 延迟
    const ttfbValues = [];
    const url = joinUrl(base, LATENCY_PKG);
    for (let i = 0; i < RUNS; i += 1) {
      const p = await probe(url, { timeout: TIMEOUT, stopAfterFirstByte: true });
      if (p.ok && p.ttfb != null) {
        ttfbValues.push(p.ttfb);
        if (result.tcp == null) {
          result.tcp = p.tcp;
          result.tls = p.tls;
        }
      } else if (!result.error) {
        result.error = p.error;
      }
    }

    result.latency = stats(ttfbValues);
    result.ok = ttfbValues.length > 0;
    return result;
  }),
);

/* ---------- 阶段二：下载测速（串行，避免抢带宽） ---------- */

if (!JSON_OUT && results.some((r) => r.ok)) {
  if (process.stdout.isTTY) process.stdout.write(gray('\n正在串行下载测速...'));
  else console.log(gray('正在串行下载测速...'));
}

for (const r of results) {
  if (!r.ok) continue;
  const tarballUrl = joinUrl(r.url, `${SPEED_PKG.name}/-/${SPEED_PKG.file}`);
  const p = await probe(tarballUrl, { timeout: SPEED_TIMEOUT });

  if (p.ok && p.bytes > 0 && p.totalMs > 0) {
    const elapsed = p.ttfb != null && p.totalMs > p.ttfb + 20 ? p.totalMs - p.ttfb : p.totalMs;
    r.speed = {
      mbps: p.bytes / 1e6 / (elapsed / 1000),
      seconds: p.totalMs / 1000,
      bytes: p.bytes,
    };
  } else if (!r.error) {
    r.error = p.error || '下载失败';
  }
}

if (!JSON_OUT && results.some((r) => r.ok)) {
  if (process.stdout.isTTY) process.stdout.write('\r\x1b[K');
}

/* ---------- 输出 ---------- */

if (JSON_OUT) {
  const round1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
  const json = results.map((r) => ({
    name: r.name,
    url: r.url,
    host: r.host,
    ip: r.ip,
    current: r.current,
    ok: r.ok,
    error: r.error,
    dnsMs: round1(r.dns),
    tcpMs: round1(r.tcp),
    tlsMs: round1(r.tls),
    latencyMs: r.latency
      ? {
          min: round1(r.latency.min),
          avg: round1(r.latency.avg),
          median: round1(r.latency.median),
          max: round1(r.latency.max),
          jitter: round1(r.latency.jitter),
        }
      : null,
    downloadMbps: round1(r.speed?.mbps),
    downloadSeconds: round1(r.speed?.seconds),
    downloadBytes: r.speed?.bytes ?? null,
  }));
  console.log(JSON.stringify(json, null, 2));
  process.exit(0);
}

// 按中位延迟排序（失败项排最后）
const ranked = [...results].sort((a, b) => {
  if (a.ok !== b.ok) return a.ok ? -1 : 1;
  if (!a.ok) return 0;
  return a.latency.median - b.latency.median;
});

const best = {
  latency: ranked.find((r) => r.ok)?.name,
  speed: [...results].filter((r) => r.speed).sort((a, b) => b.speed.mbps - a.speed.mbps)[0]?.name,
  dns: [...results].filter((r) => r.dns != null).sort((a, b) => a.dns - b.dns)[0]?.name,
};

// 表头与列宽
const headers = ['源', 'DNS', 'TCP', 'TLS', '首字节 最快/中位', '抖动', '下载速度', '结果'].concat(
  VERBOSE ? ['IP'] : [],
);

const rows = ranked.map((r) => {
  const lat = r.latency;
  const latencyCell = lat ? `${Math.round(lat.min)}/${Math.round(lat.median)}ms` : '—';
  const cell = (text, isBest) => (isBest ? green(text) : text);

  const cells = [
    `${r.current ? '*' : ' '}${r.name}`,
    cell(fmtMs(r.dns), best.dns === r.name),
    fmtMs(r.tcp),
    fmtMs(r.tls),
    cell(latencyCell, best.latency === r.name),
    lat ? `${Math.round(lat.jitter)}ms` : '—',
    cell(fmtSpeed(r.speed?.mbps), best.speed === r.name),
    r.ok ? green('✓') : red('✗'),
  ];
  if (VERBOSE) cells.push(gray(r.ip || '—'));
  return cells;
});

// 计算列宽
const widths = headers.map((h, i) =>
  Math.max(displayWidth(h), ...rows.map((row) => displayWidth(row[i] ?? ''))),
);

const rule = (left, mid, right) =>
  gray(left + widths.map((w) => '─'.repeat(w + 2)).join(mid) + right);

console.log('\n' + rule('┌', '┬', '┐'));

const headerRow = headers
  .map((h, i) => gray(center(h, widths[i])))
  .join(gray('  '));
console.log(gray('│ ') + headerRow + gray(' │'));
console.log(rule('├', '┼', '┤'));

for (const row of rows) {
  const text = row
    .map((v, i) => (i === 0 ? padEnd(v, widths[i]) : center(v, widths[i])))
    .join('  ');
  console.log(gray('│ ') + text + gray(' │'));
}

console.log(rule('└', '┴', '┘'));

// 失败原因
const failed = results.filter((r) => !r.ok);
if (failed.length) {
  console.log('');
  for (const r of failed) {
    console.log(red(`  ✗ ${r.name}：${r.error || '无法连接'}`));
  }
}

// 结论
console.log('');
if (best.latency) {
  const fastest = results.find((r) => r.name === best.latency);
  console.log(`${bold('推荐')}：${cyan(fastest.name)}  ${gray(fastest.url)}`);
  console.log(gray(`  延迟最低（中位 ${Math.round(fastest.latency.median)}ms）`));
  if (best.speed && best.speed !== best.latency) {
    const s = results.find((r) => r.name === best.speed);
    console.log(gray(`  下载最快：${s.name}（${s.speed.mbps.toFixed(2)} MB/s）`));
  }
  console.log('');
  console.log(gray('  切换命令（永久生效，用户级配置）：'));
  console.log(`  ${cyan(`npm config set registry ${fastest.url}/`)}`);
  if (fastest.current) console.log(green('  ✓ 该源已经是当前配置'));
}

if (ranked.some((r) => r.ok)) {
  console.log('');
  console.log(
    gray(
      '* 表示当前 npm 配置的源。延迟为「请求包元数据到首字节」的时间，包含 DNS/TCP/TLS 开销；' +
        '抖动 = 最慢 - 最快。',
    ),
  );
  console.log(gray('  结果受网络波动、运营商线路、CDN 节点影响，建议在不同时段多测几次。'));
  console.log('');
}

/* ------------------------------------------------------------------ */
/* 附：已知失效的旧源（如需测试可取消注释）                            */
/* ------------------------------------------------------------------ */
// { name: '阿里云(旧路径)', url: 'https://mirrors.aliyun.com/npm/' },              // 404，改用 npmmirror
// { name: '中科大 USTC',    url: 'https://mirrors.ustc.edu.cn/npm/' },             // 404，已停止 npm 镜像
// { name: '清华 TUNA',      url: 'https://mirrors.tuna.tsinghua.edu.cn/npm/' },    // 404，已停止 npm 镜像
// { name: '南京大学',        url: 'https://mirrors.nju.edu.cn/npm/' },              // 404，已停止 npm 镜像
// { name: 'cnpmjs.org',     url: 'https://registry.cnpmjs.org' },                  // 已停用
// { name: 'npm.taobao.org', url: 'https://registry.npm.taobao.org' },              // 已停用
