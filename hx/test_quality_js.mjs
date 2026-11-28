#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';

const ROOT = resolve(process.argv[2] ?? process.cwd());
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', '.artifacts', 'coverage', 'playwright-report', 'test-results', '.hx']);
const WEAK_MATCHERS = new Set(['toBeTruthy', 'toBeFalsy', 'toBeDefined']);
const TEST_DECL = /^[ \t]*(?:test|it)(?:\.(?:only|skip|todo|concurrent|fails|sequential))?(?:\.each\s*\((?:[^()]|\([^()]*\))*\))?\s*\(\s*(['"`])(.*?)\1/gm;

const toPosix = (value) => value.split(sep).join('/');

function walk(dir, out) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) walk(join(dir, entry.name), out);
    } else if (entry.isFile()) {
      out.push(toPosix(relative(ROOT, join(dir, entry.name))));
    }
  }
  return out;
}

export function expandBraces(pattern) {
  const match = /\{([^{}]+)\}/.exec(pattern);
  if (match === null) return [pattern];
  const head = pattern.slice(0, match.index);
  const tail = pattern.slice(match.index + match[0].length);
  return match[1].split(',').flatMap((part) => expandBraces(head + part + tail));
}

export function globToRegExp(glob) {
  let source = '^';
  for (let i = 0; i < glob.length; i += 1) {
    const char = glob[i];
    if (char === '*') {
      if (glob[i + 1] === '*') {
        i += 1;
        if (glob[i + 1] === '/') {
          i += 1;
          source += '(?:.*/)?';
        } else {
          source += '.*';
        }
      } else {
        source += '[^/]*';
      }
    } else if (char === '?') {
      source += '[^/]';
    } else {
      source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  return new RegExp(`${source}$`);
}

function vitestConfigs() {
  try {
    return readdirSync(ROOT, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /^vitest.*\.config\.ts$/.test(entry.name))
      .map((entry) => entry.name)
      .sort();
  } catch {
    return [];
  }
}

function configIncludes(file) {
  try {
    const text = readFileSync(join(ROOT, file), 'utf8');
    const match = /include:\s*\[([^\]]*)\]/.exec(text);
    if (match === null) return [];
    return [...match[1].matchAll(/['"]([^'"]+)['"]/g)].map((entry) => entry[1]);
  } catch {
    return [];
  }
}

function nodeTestPatterns() {
  let pkg;
  try {
    pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
  } catch {
    return [];
  }
  const patterns = new Set();
  for (const command of Object.values(pkg.scripts ?? {})) {
    const tokens = String(command).split(/\s+/);
    const start = tokens.indexOf('--test');
    if (start < 0) continue;
    for (const token of tokens.slice(start + 1)) {
      if (/^(?:&&|\|\||;|\|)$/.test(token)) break;
      if (token.startsWith('-')) continue;
      patterns.add(token.replace(/^['"]|['"]$/g, ''));
    }
  }
  return [...patterns];
}

export function suiteFiles() {
  const patterns = [
    ...vitestConfigs().flatMap(configIncludes),
    'e2e/**/*.spec.ts',
    ...nodeTestPatterns(),
  ].flatMap(expandBraces);
  const matchers = patterns.map(globToRegExp);
  return walk(ROOT, [])
    .filter((file) => matchers.some((matcher) => matcher.test(file)))
    .sort();
}

function braceBody(text, open) {
  let depth = 0;
  for (let index = open; index < text.length; index += 1) {
    if (text[index] === '{') depth += 1;
    else if (text[index] === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(open, index + 1);
    }
  }
  return '';
}

function localAssertionHelpers(text) {
  const helpers = new Set();
  const consider = (name, open, start) => {
    if (open < 0 || open - start > 500) return;
    const body = braceBody(text, open);
    if (body !== '' && /\bthrow\b|\bexpect\s*\(|\bassert\s*[.(]/.test(body)) helpers.add(name);
  };
  for (const match of text.matchAll(/function\s+([A-Za-z_$][\w$]*)\s*\(/g)) {
    consider(match[1], text.indexOf('{', match.index), match.index);
  }
  for (const match of text.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?function\s*\(/g)) {
    consider(match[1], text.indexOf('{', match.index), match.index);
  }
  for (const match of text.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>/g)) {
    const arrow = text.indexOf('=>', match.index);
    const after = arrow + 2 + (text.slice(arrow + 2).match(/^\s*/)?.[0].length ?? 0);
    if (text[after] !== '{') continue;
    consider(match[1], after, match.index);
  }
  return helpers;
}

function callsHelper(block, helpers) {
  for (const name of helpers) {
    if (new RegExp(`\\b${name}\\s*\\(`).test(block)) return true;
  }
  return false;
}

function subjectStem(file) {
  return resolve(ROOT, file.replace(/\.(test|spec)\.(ts|tsx|mjs|js)$/, '')).toLowerCase();
}

function mockStem(file, spec) {
  return resolve(ROOT, dirname(file), spec).replace(/\.(ts|tsx|js|mjs)$/, '').toLowerCase();
}

function fileMocksSubject(file, text) {
  const subject = subjectStem(file);
  for (const match of text.matchAll(/\b(?:vi|jest)\.mock\(\s*['"]([^'"]+)['"]/g)) {
    if (mockStem(file, match[1]) === subject) return true;
  }
  return false;
}

function weakOnly(block) {
  const matchers = [...block.matchAll(/\.(to[A-Za-z]+|rejects|resolves)\s*\(/g)]
    .map((match) => match[1])
    .filter((name) => name !== 'rejects' && name !== 'resolves');
  const booleanToBe = /\.toBe\(\s*(?:true|false)\s*\)/.test(block);
  const weakNot = /\.not\.(?:toBeNull|toBeUndefined)\s*\(/.test(block);
  const strong = matchers.some((name) => {
    if (WEAK_MATCHERS.has(name)) return false;
    if (name === 'toBe' && booleanToBe) return false;
    if (weakNot && (name === 'toBeNull' || name === 'toBeUndefined')) return false;
    return true;
  });
  if (strong) return false;
  const assertNames = [...block.matchAll(/\bassert\s*\.\s*([A-Za-z]+)\s*\(/g)].map((match) => match[1]);
  if (assertNames.some((name) => name !== 'ok')) return false;
  return matchers.length > 0 || assertNames.length > 0 || /\bassert\s*\(/.test(block);
}

export function analyzeSource(file, text) {
  const helpers = localAssertionHelpers(text);
  const mocksSubject = fileMocksSubject(file, text);
  const declarations = [...text.matchAll(TEST_DECL)];
  const tests = [];
  for (let index = 0; index < declarations.length; index += 1) {
    const match = declarations[index];
    const start = match.index;
    const end = index + 1 < declarations.length ? declarations[index + 1].index : text.length;
    const block = text.slice(start, end);
    const helper = callsHelper(block, helpers);
    const hasDirect = /\bexpect\s*\(/.test(block) || /\bassert\s*[.(]/.test(block);
    const rules = [];
    if (!hasDirect && !helper) rules.push('no-assertion');
    else if (!helper && hasDirect && weakOnly(block)) rules.push('truthiness-only');
    if (mocksSubject) rules.push('patches-subject');
    tests.push({
      file: toPosix(file),
      line: text.slice(0, start).split('\n').length,
      name: match[2],
      rules,
    });
  }
  return tests;
}

function main() {
  const files = suiteFiles();
  const tests = [];
  const problems = [];
  for (const file of files) {
    try {
      const absolute = join(ROOT, file);
      if (existsSync(absolute)) tests.push(...analyzeSource(file, readFileSync(absolute, 'utf8')));
    } catch (error) {
      problems.push(`${file}: ${error.message}`);
    }
  }
  console.log(JSON.stringify({ files: files.length, tests, problems }));
}

main();
