import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Libraries go in their own files so browsers cache them between app updates.
function vendorChunk(id) {
  if (!id.includes('node_modules')) return undefined
  if (/[\\/]node_modules[\\/]katex[\\/]/.test(id)) return 'katex'
  if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react'
  return 'vendor'
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/NoteScanner/',
  build: {
    rollupOptions: {
      output: { manualChunks: vendorChunk },
    },
  },
})
