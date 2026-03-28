import { readdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

const root = path.resolve('.tmp-tests')

async function listJsFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = await Promise.all(
    entries.map(async (entry) => {
      const fullPath = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        return listJsFiles(fullPath)
      }
      return fullPath.endsWith('.js') ? [fullPath] : []
    }),
  )
  return files.flat()
}

function rewriteAlias(specifier, filePath) {
  const targetPath = path.join(root, 'src', `${specifier}.js`)
  const relativePath = path.relative(path.dirname(filePath), targetPath)
  return relativePath.startsWith('.') ? relativePath : `./${relativePath}`
}

function addJsExtension(specifier) {
  if (
    specifier.endsWith('.js') ||
    specifier.endsWith('.json') ||
    specifier.endsWith('.node')
  ) {
    return specifier
  }
  return `${specifier}.js`
}

const files = await listJsFiles(root)

for (const filePath of files) {
  const source = await readFile(filePath, 'utf8')
  const rewritten = source
    .replaceAll(
      /((?:import|export)\s[^'"]*?from\s*|import\s*\()\s*['"]@\/([^'"]+)['"]/g,
      (match, prefix, specifier) => {
        const nextSpecifier = rewriteAlias(specifier, filePath)
        const quote = match.includes('"') ? '"' : "'"
        return `${prefix}${quote}${nextSpecifier}${quote}`
      },
    )
    .replaceAll(
      /((?:import|export)\s[^'"]*?from\s*|import\s*\()\s*['"](\.{1,2}\/[^'"]+)['"]/g,
      (match, prefix, specifier) => {
        const nextSpecifier = addJsExtension(specifier)
        const quote = match.includes('"') ? '"' : "'"
        return `${prefix}${quote}${nextSpecifier}${quote}`
      },
    )

  if (rewritten !== source) {
    await writeFile(filePath, rewritten)
  }
}
