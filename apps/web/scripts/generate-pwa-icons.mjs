import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const publicDirectory = fileURLToPath(new URL('../public/', import.meta.url))
const source = await readFile(new URL('../public/favicon.svg', import.meta.url), 'utf8')
const browser = await chromium.launch()
try {
  const page = await browser.newPage()
  for (const size of [192, 512]) {
    const encoded = await page.evaluate(async ({ svg, size: pixels }) => {
      const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
      try {
        const image = new Image()
        image.src = url
        await image.decode()
        const canvas = document.createElement('canvas')
        canvas.width = pixels
        canvas.height = pixels
        canvas.getContext('2d').drawImage(image, 0, 0, pixels, pixels)
        return canvas.toDataURL('image/png').split(',')[1]
      } finally {
        URL.revokeObjectURL(url)
      }
    }, { svg: source, size })
    await writeFile(`${publicDirectory}pwa-icon-${size}.png`, Buffer.from(encoded, 'base64'))
  }
} finally {
  await browser.close()
}
