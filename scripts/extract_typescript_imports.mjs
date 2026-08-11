import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { createRequire } from 'node:module'

const [, , compilerPath, targetRoot] = process.argv

if (!compilerPath || !targetRoot) {
  process.stderr.write('usage: node extract_typescript_imports.mjs <typescript.js> <target-root>\n')
  process.exit(2)
}

const require = createRequire(import.meta.url)
const ts = require(path.resolve(compilerPath))
const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
const dependencies = []
const symbols = []
const errors = []

function visibility(node) {
  const modifiers = ts.canHaveModifiers(node) ? ts.getModifiers(node) || [] : []
  return modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword || modifier.kind === ts.SyntaxKind.DefaultKeyword)
    ? 'public'
    : 'internal'
}

function recordSymbol(sourceFile, node, relativePath, kind, nameNode) {
  if (!nameNode || !nameNode.text) return
  const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile))
  const end = sourceFile.getLineAndCharacterOfPosition(node.getEnd())
  symbols.push({
    file: relativePath,
    kind,
    name: String(nameNode.text),
    lineStart: start.line + 1,
    lineEnd: end.line + 1,
    visibility: visibility(node),
  })
}

function visit(sourceFile, node, relativePath) {
  let moduleSpecifier = null
  if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
    moduleSpecifier = node.moduleSpecifier
  } else if (ts.isCallExpression(node) && node.arguments.length > 0) {
    const expression = node.expression
    const isDynamicImport = expression.kind === ts.SyntaxKind.ImportKeyword
    const isRequire = ts.isIdentifier(expression) && expression.text === 'require'
    if (isDynamicImport || isRequire) {
      moduleSpecifier = node.arguments[0]
    }
  }
  if (moduleSpecifier && ts.isStringLiteralLike(moduleSpecifier)) {
    const position = sourceFile.getLineAndCharacterOfPosition(moduleSpecifier.getStart(sourceFile))
    dependencies.push({
      file: relativePath,
      reference: moduleSpecifier.text,
      line: position.line + 1,
    })
  }
  if (ts.isFunctionDeclaration(node)) {
    recordSymbol(sourceFile, node, relativePath, 'function', node.name)
  } else if (ts.isClassDeclaration(node)) {
    recordSymbol(sourceFile, node, relativePath, 'class', node.name)
  } else if (ts.isInterfaceDeclaration(node)) {
    recordSymbol(sourceFile, node, relativePath, 'interface', node.name)
  } else if (ts.isTypeAliasDeclaration(node)) {
    recordSymbol(sourceFile, node, relativePath, 'type', node.name)
  } else if (ts.isEnumDeclaration(node)) {
    recordSymbol(sourceFile, node, relativePath, 'enum', node.name)
  } else if (ts.isVariableStatement(node)) {
    for (const declaration of node.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name)) {
        recordSymbol(sourceFile, node, relativePath, 'variable', declaration.name)
      }
    }
  }
  ts.forEachChild(node, (child) => visit(sourceFile, child, relativePath))
}

for (const relativePath of payload.files || []) {
  const absolutePath = path.resolve(targetRoot, relativePath)
  const sourceText = fs.readFileSync(absolutePath, 'utf8')
  const sourceFile = ts.createSourceFile(
    absolutePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    ts.getScriptKindFromFileName(absolutePath),
  )
  for (const diagnostic of sourceFile.parseDiagnostics || []) {
    errors.push({
      file: relativePath,
      message: ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'),
    })
  }
  visit(sourceFile, sourceFile, relativePath)
}

process.stdout.write(JSON.stringify({ compilerVersion: ts.version, dependencies, symbols, errors }))
