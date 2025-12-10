// 파일 업로드 검증 유틸리티

const MAX_FILE_SIZE = 100 * 1024 * 1024 // 100MB
const ALLOWED_AUDIO_TYPES = [
  'audio/webm',
  'audio/wav',
  'audio/mp3',
  'audio/mpeg',
  'audio/mp4',
  'audio/m4a',
  'audio/ogg',
  'audio/x-m4a',
  'audio/x-wav'
]

const ALLOWED_AUDIO_EXTENSIONS = [
  '.webm',
  '.wav',
  '.mp3',
  '.m4a',
  '.ogg',
  '.mp4'
]

export interface FileValidationResult {
  valid: boolean
  error?: string
}

/**
 * 오디오 파일 유효성 검증
 */
export function validateAudioFile(file: File): FileValidationResult {
  // 파일 존재 체크
  if (!file) {
    return { valid: false, error: '파일을 선택해주세요.' }
  }

  // 파일 크기 체크
  if (file.size === 0) {
    return { valid: false, error: '빈 파일은 업로드할 수 없습니다.' }
  }

  if (file.size > MAX_FILE_SIZE) {
    const sizeMB = (MAX_FILE_SIZE / (1024 * 1024)).toFixed(0)
    return { 
      valid: false, 
      error: `파일 크기는 ${sizeMB}MB를 초과할 수 없습니다.\n현재 파일: ${(file.size / (1024 * 1024)).toFixed(1)}MB` 
    }
  }

  // 파일 확장자 체크
  const fileExtension = file.name.toLowerCase().match(/\.[^.]+$/)
  if (!fileExtension || !ALLOWED_AUDIO_EXTENSIONS.includes(fileExtension[0])) {
    return {
      valid: false,
      error: `지원하지 않는 파일 형식입니다.\n지원 형식: ${ALLOWED_AUDIO_EXTENSIONS.join(', ')}`
    }
  }

  // MIME 타입 체크 (브라우저가 제공하는 경우)
  if (file.type && !ALLOWED_AUDIO_TYPES.includes(file.type)) {
    return {
      valid: false,
      error: `오디오 파일만 업로드 가능합니다.\n파일 타입: ${file.type}`
    }
  }

  return { valid: true }
}

/**
 * Blob 유효성 검증
 */
export function validateBlob(blob: Blob): FileValidationResult {
  if (!blob) {
    return { valid: false, error: '녹음된 데이터가 없습니다.' }
  }

  if (blob.size === 0) {
    return { valid: false, error: '녹음된 데이터가 비어있습니다.' }
  }

  if (blob.size > MAX_FILE_SIZE) {
    return {
      valid: false,
      error: `녹음 파일이 너무 큽니다. (${(blob.size / (1024 * 1024)).toFixed(1)}MB)`
    }
  }

  return { valid: true }
}

/**
 * 파일 크기 포맷팅
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

