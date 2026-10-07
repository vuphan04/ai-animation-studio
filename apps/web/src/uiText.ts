import { t } from './i18n'
const states: Record<string, string> = {
  CURRENT: t('Hiện hành'), STALE: t('Đã cũ'), NEEDS_REVIEW: t('Cần duyệt'), APPROVED: t('Đã duyệt'), REJECTED: t('Đã từ chối'),
  QUEUED: t('Đang chờ'), RUNNING: t('Đang chạy'), COMPLETED: t('Hoàn tất'), PENDING: t('Chờ nhà cung cấp'),
  BLOCKED: t('Bị chặn'), RECONCILING: t('Đang đối soát'), FAILED: t('Thất bại'), CANCELLED: t('Đã hủy'),
  MAPPED: t('Đã chọn asset'), MISSING: t('Thiếu'), RESERVED: t('Đang giữ'), SETTLED: t('Đã tính'), RELEASED: t('Đã nhả'), DRAFT_READY: t('Có bản nháp'), CAPTURED: t('Đã chụp')
}

const errors: Record<string, string> = {
  'name is required': t('Cần nhập tên dự án.'),
  'episode name and nonnegative ordinal required': t('Cần nhập tên tập hợp lệ.'),
  'database uniqueness or relationship conflict': t('Dữ liệu bị trùng hoặc có quan hệ không hợp lệ.'),
  'loopback only': t('Chỉ cho phép truy cập từ máy này.'),
  'origin denied': t('Nguồn truy cập không được phép.'),
  'local session required': t('Phiên làm việc đã hết. Hãy tải lại trang.'),
  'session token required for mutation': t('Thiếu quyền thay đổi dữ liệu. Hãy tải lại trang.'),
  'relative_path must be relative to the project root': t('Đường dẫn phải nằm trong thư mục dự án.'),
  'relative_path escapes project root': t('Đường dẫn vượt ra ngoài thư mục dự án.')
}

const resources: Record<string, string> = {
  project: t('Dự án'), episode: t('Tập'), shot: 'Shot', scene: t('Cảnh'), entity: t('Nhân vật hoặc bối cảnh'), screenplay: t('Kịch bản'), breakdown: 'Breakdown',
  scan: t('Lần quét asset'), reference: t('Ảnh tham chiếu'), proxy: 'Proxy', 'rough cut': 'Rough cut', delivery: t('Gói bàn giao'), 'sound clip': t('Clip âm thanh'), 'sound build': t('Bản dựng âm thanh')
}

export const stateLabel = (value: string) => states[value] ?? value

export function errorLabel(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  if (error instanceof SyntaxError || /Unexpected token|Expected property name|not valid JSON/i.test(raw)) {
    return t('Máy chủ trả về dữ liệu không đọc được. Tải lại trang rồi thử lại.')
  }
  if (/Failed to fetch|NetworkError|fetch failed/i.test(raw)) return t('Không kết nối được máy chủ local. Hãy kiểm tra cửa sổ chạy app.')
  if (errors[raw]) return errors[raw]
  if (/^\[\{.*"type"/.test(raw)) return t('Dữ liệu gửi lên không hợp lệ. Kiểm tra các trường bắt buộc và kiểu dữ liệu.')
  const missing = raw.match(/^(.+) not found$/)
  if (missing) return t('{0} không còn tồn tại. Tải lại rồi thử lại.', resources[missing[1]] ?? t('Mục được yêu cầu'))
  return t('Không thực hiện được thao tác. Chi tiết kỹ thuật: {0}', raw)
}
