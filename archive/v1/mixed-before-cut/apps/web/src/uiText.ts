const subsystems: Record<string, string> = {
  body: 'Chuyển động', face: 'Gương mặt', audio: 'Thoại trong cảnh', camera: 'Máy quay',
  environment: 'Bối cảnh', hair: 'Tóc', cloth: 'Trang phục',
  vfx: 'Hiệu ứng', render: 'Kết xuất', sound: 'Âm thanh'
}

const assetKinds: Record<string, string> = {
  character: 'Nhân vật', environment: 'Bối cảnh', style_reference: 'Phong cách hình ảnh', anchor: 'Điểm neo',
  animation: 'Hoạt ảnh', paired_animation: 'Hoạt ảnh cặp', dialogue: 'Thoại', voice_profile: 'Hồ sơ giọng đọc', retarget_profile: 'Hồ sơ chuyển khung xương',
  render_profile: 'Hồ sơ kết xuất', groom: 'Tóc/Groom', costume: 'Trang phục/Cloth', niagara_effect: 'Hiệu ứng Niagara'
}

const states: Record<string, string> = {
  AUTO: 'Tự động', MANUAL: 'Thủ công', LOCKED: 'Đã khóa', UNLOCKED: 'Chưa khóa',
  CURRENT: 'Hiện hành', STALE: 'Đã cũ', NEEDS_REVIEW: 'Cần duyệt', APPROVED: 'Đã duyệt', REJECTED: 'Đã từ chối',
  QUEUED: 'Đang chờ', RUNNING: 'Đang chạy', COMPLETED: 'Hoàn tất', PENDING: 'Chờ nhà cung cấp',
  PARTIAL_COPY: 'Sao chép chưa đủ', BUILDING: 'Đang xuất gói', CORRUPT: 'Checksum không khớp', FILES_READY_UNVERIFIED_RESOLVE: 'Đã xuất file · chưa kiểm chứng Resolve',
  BLOCKED: 'Bị chặn', RECONCILING: 'Đang đối soát', FAILED: 'Thất bại', CANCELLED: 'Đã hủy',
  MAPPED: 'Đã chọn asset', MISSING: 'Thiếu', RESERVED: 'Đang giữ', SETTLED: 'Đã tính', RELEASED: 'Đã nhả', DRAFT_READY: 'Có bản nháp', CAPTURED: 'Đã chụp'
}

const routes: Record<string, string> = {
  UNREAL: 'Unreal', SEEDANCE: 'Seedance', HYBRID: 'Kết hợp'
}

const errors: Record<string, string> = {
  'name is required': 'Cần nhập tên dự án.',
  'episode name and nonnegative ordinal required': 'Cần nhập tên tập hợp lệ.',
  'invalid registry ID or kind': 'ID hoặc loại tài nguyên không hợp lệ.',
  'metadata contains forbidden secret field': 'Metadata chứa trường bí mật không được phép.',
  'manifest must be an object': 'Manifest phải là một đối tượng JSON.',
  'schema_version must be 1.0': 'Manifest phải dùng phiên bản schema 1.0.',
  'shot_id is required': 'Manifest thiếu shot_id.',
  'fps.num and fps.den must be positive integers': 'Tử số và mẫu số FPS phải là số nguyên dương.',
  'timeline must be a nonempty [in,out_exclusive) frame range': 'Khoảng frame [in, out_exclusive) phải có độ dài dương.',
  'route.suggested must be UNREAL, SEEDANCE or HYBRID': 'Tuyến đề xuất phải là UNREAL, SEEDANCE hoặc HYBRID.',
  'performances must be an array': 'performances phải là một danh sách.',
  'camera_intent must be an object': 'camera_intent phải là một đối tượng JSON.',
  'camera_intent.shot_size must be a nonempty string': 'Cần nhập shot_size của máy quay.',
  'intentional_overlap_with must be an array of shot IDs': 'intentional_overlap_with phải là danh sách ID cảnh quay.',
  'intentional_overlap_with contains duplicate or unknown shot ID': 'Danh sách cảnh chồng khung có ID trùng hoặc không tồn tại.',
  'shot revision conflict': 'Phiên bản cảnh quay đã thay đổi. Tải lại trước khi sửa.',
  'shot_id cannot change': 'Không thể đổi shot_id của cảnh quay hiện có.',
  'manifest unchanged': 'Manifest chưa có thay đổi.',
  'invalid approved route': 'Tuyến được duyệt không hợp lệ.',
  'approval requires exact current revision': 'Chỉ được duyệt đúng phiên bản cảnh quay hiện hành.',
  'invalid subsystem state': 'Trạng thái thành phần không hợp lệ.',
  'subsystem state revision conflict': 'Trạng thái thành phần đã thay đổi. Tải lại trước khi sửa.',
  'adopt requires MANUAL + LOCKED and a fingerprint': 'Tiếp nhận chỉnh sửa thủ công cần chế độ Thủ công, Đã khóa và fingerprint.',
  'adopt fingerprint differs from observed external content': 'Fingerprint khác nội dung bên ngoài đã quan sát.',
  'external edit conflict; adopt and lock before automation': 'Có chỉnh sửa bên ngoài. Hãy tiếp nhận và khóa trước khi tự động hóa.',
  'subsystem and fingerprint required': 'Cần thành phần và fingerprint.',
  'unknown subsystem': 'Thành phần không tồn tại.',
  'approval requires exact shot and state revision': 'Chỉ được duyệt đúng phiên bản cảnh quay và trạng thái.',
  'external edit conflict must be adopted first': 'Cần tiếp nhận chỉnh sửa bên ngoài trước.',
  'stale automation output requires rebuild': 'Đầu ra tự động đã cũ; cần tạo lại trước khi duyệt.',
  'profile_version and known resource required': 'Cần phiên bản hồ sơ và loại tài nguyên xử lý hợp lệ.',
  'same operation ID has different dependencies or resource': 'Operation ID đã tồn tại với dependency hoặc tài nguyên khác.',
  'job is not reconciling': 'Công việc này không ở trạng thái cần đối soát.',
  'retry requires a known pre-write block and no provider request or receipt': 'Chỉ thử lại khi biết chắc công việc bị chặn trước khi ghi và chưa có request hoặc receipt.',
  'database uniqueness or relationship conflict': 'Dữ liệu bị trùng hoặc có quan hệ không hợp lệ.',
  'loopback only': 'Chỉ cho phép truy cập từ máy này.',
  'origin denied': 'Nguồn truy cập không được phép.',
  'local session required': 'Phiên làm việc đã hết. Hãy tải lại trang.',
  'session token required for mutation': 'Thiếu quyền thay đổi dữ liệu. Hãy tải lại trang.',
  'shot revision changed': 'Phiên bản cảnh quay đã thay đổi.',
  'exact plan approval missing': 'Chưa duyệt kế hoạch cho đúng phiên bản.',
  'legacy revision has no pinned registry; create new revision': 'Phiên bản cũ chưa gắn phiên bản thư viện; hãy tạo phiên bản cảnh quay mới.',
  'manual or locked subsystem': 'Thành phần đang ở chế độ thủ công hoặc đã khóa.',
  'external edit fingerprint conflict': 'Fingerprint cho thấy nội dung đã được sửa bên ngoài.',
  'shot revision changed after enqueue': 'Phiên bản cảnh quay đã đổi sau khi xếp việc.',
  'registry version changed after enqueue': 'Phiên bản thư viện đã đổi sau khi xếp việc.',
  'subsystem revision/mode/lock changed after enqueue': 'Phiên bản, chế độ hoặc khóa đã đổi sau khi xếp việc.',
  'external edit fingerprint conflict before write': 'Có chỉnh sửa bên ngoài trước khi ghi.',
  'dependency no longer complete': 'Công việc phụ thuộc không còn hoàn tất.',
  'dependency failed or requires reconcile': 'Công việc phụ thuộc thất bại hoặc cần đối soát.',
  'backend restarted during attempt': 'Backend đã khởi động lại trong lúc xử lý.',
  'relative_path must be relative to the project root': 'Đường dẫn phải nằm trong thư mục dự án.',
  'relative_path escapes project root': 'Đường dẫn vượt ra ngoài thư mục dự án.',
  'fake adapter receipt failed expected-output validation': 'Biên nhận mô phỏng không khớp đầu ra mong đợi.'
}

export const subsystemLabel = (value: string) => subsystems[value] ?? value
export const assetKindLabel = (value: string) => assetKinds[value] ?? value
export const stateLabel = (value: string) => states[value] ?? value
export const routeLabel = (value: string) => routes[value] ?? value
export const adapterLabel = (value: string) => value === 'FAKE_CONTROL_FLOW' ? 'Mô phỏng' : value === 'UNREAL_EDITOR_PYTHON' ? 'Unreal Editor Python' : value

export function errorLabel(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  if (error instanceof SyntaxError || /Unexpected token|Expected property name|not valid JSON/i.test(raw)) {
    return 'JSON không hợp lệ. Kiểm tra dấu ngoặc, dấu phẩy và dấu nháy.'
  }
  if (/Failed to fetch|NetworkError|fetch failed/i.test(raw)) return 'Không kết nối được máy chủ local. Hãy kiểm tra cửa sổ chạy app.'
  if (errors[raw]) return errors[raw]
  if (/^\[\{.*"type"/.test(raw)) return 'Dữ liệu gửi lên không hợp lệ. Kiểm tra các trường bắt buộc và kiểu dữ liệu.'
  const registry = raw.match(/^registry (.+) changed; create and approve new revision$/)
  if (registry) return `Tài nguyên ${registry[1]} đã đổi. Hãy tạo và duyệt phiên bản cảnh quay mới.`
  const forbidden = raw.match(/^(.+) is not allowed in a draft manifest$/)
  if (forbidden) return `${forbidden[1]} không được phép trong manifest nháp.`
  const unsupported = raw.match(/^(.+): unsupported field (.+)$/)
  if (unsupported) return `${unsupported[1]}: trường ${unsupported[2]} không được hỗ trợ.`
  const unknownId = raw.match(/^(.+): unknown or wrong-kind registry ID (.+)$/)
  if (unknownId) return `${unknownId[1]}: ID thư viện ${unknownId[2]} không tồn tại hoặc sai loại.`
  const incompatible = raw.match(/^(.+)\.animation_id incompatible with character skeleton$/)
  if (incompatible) return `${incompatible[1]}: hoạt ảnh không tương thích với khung xương nhân vật.`
  const missingDialogue = raw.match(/^(.+)\.dialogue_start_local_frame requires dialogue_id$/)
  if (missingDialogue) return `${missingDialogue[1]}: cần dialogue_id khi đặt khung bắt đầu thoại.`
  const incompleteDialogue = raw.match(/^(.+)\.dialogue timing is incomplete$/)
  if (incompleteDialogue) return `${incompleteDialogue[1]}: thời gian thoại chưa đầy đủ.`
  const overflow = raw.match(/^(.+)\.dialogue overflows shot by (\d+) frames$/)
  if (overflow) return `${overflow[1]}: thoại vượt cảnh quay ${overflow[2]} khung hình.`
  const overlap = raw.match(/^timeline overlaps (.+) without intentional_overlap_with$/)
  if (overlap) return `Dòng thời gian chồng lên ${overlap[1]} nhưng chưa khai báo intentional_overlap_with.`
  const fps = raw.match(/^fps differs from adjacent shot (.+)$/)
  if (fps) return `FPS khác cảnh quay liền kề ${fps[1]}.`
  const unavailableDependency = raw.match(/^dependency (.+) unavailable in project$/)
  if (unavailableDependency) return `Công việc phụ thuộc ${unavailableDependency[1]} không khả dụng trong dự án.`
  const missing = raw.match(/^(project|episode|shot|job) not found$/)
  if (missing) return `${{ project: 'Dự án', episode: 'Tập', shot: 'Cảnh quay', job: 'Công việc' }[missing[1]]} không tồn tại.`
  return `Không thực hiện được thao tác. Chi tiết kỹ thuật: ${raw}`
}
