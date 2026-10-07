// Keeps the English interface complete: every Vietnamese string in src/ goes through t() and has an entry in src/en.ts.
// Run by `npm run build`. `--dump <file>` writes the entries still missing as JSON.
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const src = path.join(__dirname, '..', 'src')
// Latin letters with diacritics; the multiplication and division signs sit inside that range and are left out.
const VIETNAMESE = /[À-ÖØ-öø-ɏḀ-ỿ]/
const parse = name => ts.createSourceFile(name, fs.readFileSync(path.join(src, name), 'utf8'), ts.ScriptTarget.Latest, true, name.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
const slots = text => [...new Set(text.match(/\{\d+\}/g) ?? [])].sort().join()

const table = new Map()
ts.forEachChild(parse('en.ts'), function read(node) {
  if (ts.isPropertyAssignment(node) && ts.isStringLiteralLike(node.name) && ts.isStringLiteralLike(node.initializer)) table.set(node.name.text, node.initializer.text)
  ts.forEachChild(node, read)
})

const used = new Map(), problems = []
for (const name of fs.readdirSync(src).filter(f => /\.tsx?$/.test(f) && f !== 'en.ts' && f !== 'i18n.ts').sort()) {
  const file = parse(name)
  const at = node => `${name}:${file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1}`
  ts.forEachChild(file, function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't') {
      const first = node.arguments[0]
      if (!first || !ts.isStringLiteralLike(first)) problems.push(`${at(node)} t() needs a plain string as its first argument`)
      else { used.set(first.text.trim(), at(node)); node.arguments.slice(1).forEach(visit) }
      return
    }
    const text = ts.isJsxText(node) ? node.getFullText(file) : ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node) ? node.text : ''
    if (VIETNAMESE.test(text) && !ts.isLiteralTypeNode(node.parent)) problems.push(`${at(node)} Vietnamese text outside t(): ${text.trim().slice(0, 60)}`)
    ts.forEachChild(node, visit)
  })
}

const missing = [...used.keys()].filter(key => !table.has(key))
for (const [key, where] of used) {
  if (table.has(key) && slots(key) !== slots(table.get(key))) problems.push(`${where} placeholders differ in en.ts for: ${key.slice(0, 60)}`)
}
for (const key of table.keys()) if (!used.has(key)) problems.push(`en.ts has an entry nothing uses: ${key.slice(0, 60)}`)
const dump = process.argv.indexOf('--dump')
if (dump > 0) fs.writeFileSync(process.argv[dump + 1], JSON.stringify(missing, null, 1))
for (const key of missing.slice(0, 20)) problems.push(`${used.get(key)} no English text for: ${key.slice(0, 60)}`)
if (missing.length > 20) problems.push(`…and ${missing.length - 20} more strings without English text`)
if (problems.length) { console.error(problems.join('\n')); console.error(`i18n check failed: ${used.size} strings, ${missing.length} without English text.`); process.exit(1) }
console.log(`i18n check passed: ${used.size} strings, all with English text.`)
