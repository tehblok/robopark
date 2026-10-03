import { readFile, readdir, rm, stat, utimes, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

// Compress once during the build, not on every first visit through the tunnel.
// HTML/PWA metadata keep their own revalidation policy; binary media is already compressed.
export async function compressAssets(distDirectory) {
  const summary = { files: 0, sourceBytes: 0, gzipBytes: 0 }
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) await visit(path)
      else if (entry.isFile() && /\.(?:js|css|svg)$/.test(entry.name)) {
        const metadata = await stat(path)
        const source = await readFile(path)
        const compressed = source.length >= 1024 ? gzipSync(source, { level: 9 }) : null
        // Also remove old or symlinked companions when rerunning over an existing build.
        await rm(`${path}.gz`, { force: true })
        if (compressed && compressed.length < source.length) {
          await writeFile(`${path}.gz`, compressed, { flag: 'wx' })
          await utimes(`${path}.gz`, metadata.atime, metadata.mtime)
          summary.files += 1
          summary.sourceBytes += source.length
          summary.gzipBytes += compressed.length
        }
      }
    }
  }
  await visit(join(resolve(distDirectory), 'assets'))
  return summary
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')
  console.log('Precompressed assets:', await compressAssets(dist))
}
