import { loader } from '@guolao/vue-monaco-editor'
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import CssWorker from 'monaco-editor/language/css/css.worker.js?worker'
import HtmlWorker from 'monaco-editor/language/html/html.worker.js?worker'
import JsonWorker from 'monaco-editor/language/json/json.worker.js?worker'
import TypeScriptWorker from 'monaco-editor/language/typescript/ts.worker.js?worker'

globalThis.MonacoEnvironment = {
  getWorker(_id, label) {
    switch (label) {
      case 'css':
      case 'less':
      case 'scss':
        return new CssWorker()
      case 'html':
      case 'handlebars':
      case 'razor':
        return new HtmlWorker()
      case 'json':
        return new JsonWorker()
      case 'javascript':
      case 'typescript':
        return new TypeScriptWorker()
      default:
        return new EditorWorker()
    }
  },
}

loader.config({ monaco })

export function languageForPath(path: string) {
  const name = path.split(/[/\\]/).at(-1)?.toLowerCase() ?? ''
  const languages = monaco.languages.getLanguages()
  const exact = languages.find((language) =>
    language.filenames?.some((filename) => filename.toLowerCase() === name),
  )
  if (exact) return exact.id
  return (
    languages
      .flatMap(({ id, extensions }) => (extensions ?? []).map((extension) => ({ id, extension })))
      .filter(({ extension }) => name.endsWith(extension.toLowerCase()))
      .sort((a, b) => b.extension.length - a.extension.length)[0]?.id ?? 'plaintext'
  )
}
