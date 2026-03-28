import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'

const outputRoot = path.resolve('.tmp-tests')
const sourceRoot = path.join(outputRoot, 'src')

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = await Promise.all(
    entries.map(async (entry) => {
      const resolved = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        return walk(resolved)
      }
      return resolved
    }),
  )

  return files.flat()
}

async function exists(targetPath) {
  try {
    await stat(targetPath)
    return true
  } catch {
    return false
  }
}

async function resolveSpecifier(filePath, specifier) {
  let absoluteTarget = ''

  if (specifier.startsWith('@/')) {
    absoluteTarget = path.join(sourceRoot, specifier.slice(2))
  } else if (specifier.startsWith('./') || specifier.startsWith('../')) {
    absoluteTarget = path.resolve(path.dirname(filePath), specifier)
  } else {
    return specifier
  }

  const jsTarget = `${absoluteTarget}.js`
  if (await exists(jsTarget)) {
    const relativeTarget = path.relative(path.dirname(filePath), jsTarget).split(path.sep).join('/')
    return relativeTarget.startsWith('.') ? relativeTarget : `./${relativeTarget}`
  }

  const indexTarget = path.join(absoluteTarget, 'index.js')
  if (await exists(indexTarget)) {
    const relativeTarget = path.relative(path.dirname(filePath), indexTarget).split(path.sep).join('/')
    return relativeTarget.startsWith('.') ? relativeTarget : `./${relativeTarget}`
  }

  return specifier
}

async function rewriteFile(filePath) {
  const original = await readFile(filePath, 'utf8')
  const importPattern =
    /((?:import|export)\s[\s\S]*?\sfrom\s*|import\s*\(\s*|export\s*\*\s*from\s*)(['"])([^'"]+)\2/g

  let rewritten = ''
  let lastIndex = 0

  for (const match of original.matchAll(importPattern)) {
    const [full, prefix, quote, specifier] = match
    const start = match.index ?? 0
    const end = start + full.length
    const nextSpecifier = await resolveSpecifier(filePath, specifier)

    rewritten += original.slice(lastIndex, start)
    rewritten += `${prefix}${quote}${nextSpecifier}${quote}`
    lastIndex = end
  }

  rewritten += original.slice(lastIndex)

  if (rewritten !== original) {
    await writeFile(filePath, rewritten)
  }
}

const files = await walk(outputRoot)
await Promise.all(files.filter((filePath) => filePath.endsWith('.js')).map(rewriteFile))
