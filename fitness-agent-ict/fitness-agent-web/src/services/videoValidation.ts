export const MAX_VIDEO_SIZE_MB = 100
export const MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024
export const VIDEO_ACCEPT = '.mp4,.webm,video/mp4,video/webm'

const mimeByExtension: Record<string, string> = { mp4: 'video/mp4', webm: 'video/webm' }

// accept 只帮助文件选择器过滤，实际选入和拖入的文件都要经过此处校验。
export function validateVideoFile(file: File): string | null {
  if (file.size === 0) return '这个文件是空的，请选择包含视频内容的文件。'
  if (file.size > MAX_VIDEO_SIZE_BYTES)
    return `视频超过 ${MAX_VIDEO_SIZE_MB} MB，请选择更小的文件。`

  const extension = file.name.split('.').at(-1)?.toLowerCase() ?? ''
  if (!Object.hasOwn(mimeByExtension, extension)) return '暂时只支持 MP4 和 WebM 视频。'
  if (
    file.type &&
    file.type !== 'application/octet-stream' &&
    file.type !== mimeByExtension[extension]
  ) {
    return '文件类型与扩展名不匹配，请选择原始的 MP4 或 WebM 视频。'
  }
  return null
}
