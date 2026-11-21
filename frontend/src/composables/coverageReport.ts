function readRow(value: unknown) {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('qualname' in value) ||
    typeof value.qualname !== 'string' ||
    !('kind' in value) ||
    typeof value.kind !== 'string' ||
    !('file' in value) ||
    typeof value.file !== 'string' ||
    !('line' in value) ||
    typeof value.line !== 'number' ||
    !Number.isSafeInteger(value.line) ||
    value.line < 1 ||
    !('missing' in value) ||
    !Array.isArray(value.missing) ||
    !value.missing.every((tag: unknown): tag is string => typeof tag === 'string')
  )
    return undefined

  const { qualname, kind, file, line, missing } = value
  return { qualname, kind, file, line, missing }
}

// Tool results are opaque to OpenAPI. Validate what the report needs before rendering it.
export function readCoverageReport(value: unknown) {
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value)
    } catch (error) {
      if (error instanceof SyntaxError) return undefined
      throw error
    }
  }
  if (
    typeof value !== 'object' ||
    value === null ||
    !('coverage_percent' in value) ||
    typeof value.coverage_percent !== 'number' ||
    !Number.isFinite(value.coverage_percent) ||
    value.coverage_percent < 0 ||
    value.coverage_percent > 100 ||
    !('items' in value) ||
    !Array.isArray(value.items)
  )
    return undefined

  const rows = value.items.map(readRow)
  if (!rows.every((row) => row !== undefined)) return undefined
  return { percent: value.coverage_percent, rows }
}

export type CoverageData = NonNullable<ReturnType<typeof readCoverageReport>>
