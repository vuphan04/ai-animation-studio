# AI Animation Studio

**Không gian điều phối sản xuất phim 3D trên Windows, kết hợp Unreal Engine với dịch vụ AI qua API.**

AI Animation Studio giúp người làm phim quản lý dự án, tập phim, cảnh quay, tài nguyên và các bước duyệt trong cùng một giao diện tiếng Việt. Ứng dụng lưu lịch sử phiên bản và công việc để người vận hành biết cảnh nào đang hiện hành, cần duyệt, đã lỗi thời hoặc đang bị chặn.

Mục tiêu là nối các công đoạn từ kịch bản đến bàn giao: lập kế hoạch, lắp dựng cảnh, diễn xuất, giọng thoại, máy quay, mô phỏng, kết xuất, âm thanh và đóng gói media. Người dùng quyết định kế hoạch, tuyến sản xuất, ngân sách và chất lượng cuối cùng.

> Đây là mã nguồn đang phát triển. Một số luồng hiện dùng bộ mô phỏng hoặc mới có hợp đồng dữ liệu và điều kiện kiểm tra. Bản hiện tại chưa được nghiệm thu toàn bộ trên Unreal, dịch vụ video AI và DaVinci Resolve. Cài đặt thành công hoặc lưu API key không đồng nghĩa mọi công đoạn sản xuất đã hoạt động thực tế.

## 1. Đối tượng sử dụng

- Nhà làm phim độc lập hoặc studio nhỏ muốn tổ chức nhiều cảnh theo cùng quy trình.
- Người dùng Unreal Engine cần quản lý phiên bản, approval và nội dung do nghệ sĩ chỉnh tay.
- Nhóm phát triển muốn xây dựng pipeline có thể theo dõi, thử lại và đối soát kết quả.

Phạm vi hiện tại hướng tới **một máy Windows, một người vận hành**. Giao diện mở trong trình duyệt; backend và dữ liệu chạy trên máy đó. Chưa có hệ thống cộng tác nhiều người hoặc bản đóng gói desktop hoàn chỉnh.

## 2. Các khu vực làm việc

| Khu vực | Chức năng |
|---|---|
| **Dự án** | Tạo dự án, thêm tập phim, quản lý thư mục dữ liệu của dự án. |
| **Thư viện** | Đăng ký ID và phiên bản tài nguyên; lưu thông tin khung xương, tương thích và nhãn chuyển động. |
| **Lập kế hoạch** | Lưu kịch bản, tạo nháp kế hoạch, xem cảnh và yêu cầu tài nguyên chưa giải quyết; xem khác biệt trước khi áp dụng. |
| **Bảng cảnh** | Xem các cảnh, phạm vi frame, tuyến sản xuất, revision và trạng thái. |
| **Chi tiết & khóa** | Quản lý chế độ tự động/thủ công, khóa và trạng thái đồng bộ của từng thành phần. |
| **Duyệt** | Duyệt kế hoạch và thành phần theo phiên bản cụ thể. |
| **Giọng & nhịp** | Quản lý take, review, voice lock và timing. Bộ tạo giọng hiện có là âm mẫu tổng hợp. |
| **Cache & render** | Quản lý đầu vào cache, fingerprint, phạm vi frame và kiểm kê output; kiểm điều kiện trước final render. |
| **Video & bàn giao** | Quản lý tuyến UE/AI/Hybrid, reference package, take, Picture Lock, stem và package trong phạm vi đã triển khai. |
| **Công việc** | Xếp việc, theo dõi trạng thái, lỗi, biên nhận và đối soát. Công việc mô phỏng có nhãn rõ. |
| **Nghiệm thu & sao lưu** | Quản lý hồ sơ run, baseline, evidence, rubric chất lượng và bản sao dữ liệu. |
| **Cài đặt** | Thiết lập Unreal, dịch vụ AI và media provider; lưu trạng thái cấu hình trên máy. |

Giao diện có công tắc **Ban ngày/Ban đêm**, lưu lựa chọn trong trình duyệt. Các cấu hình vận hành được thực hiện qua ứng dụng; không cần đưa API key vào mã nguồn hoặc biến môi trường.

## 3. AI hoạt động như thế nào?

Ứng dụng hướng tới **gọi dịch vụ AI qua API**, không chạy mô hình suy luận AI local. Backend có adapter OpenAI cho bước lập kế hoạch. Người dùng chọn nhà cung cấp, mô hình, giới hạn đầu ra và hạn mức ngay trong giao diện.

Luồng lập kế hoạch:

1. Tạo dự án và tập phim; chuẩn bị thư viện ID tài nguyên.
2. Nhập kịch bản và lưu phiên bản.
3. Chọn dịch vụ AI đã cấu hình; cấp ngân sách cho yêu cầu lập kế hoạch.
4. Chủ động yêu cầu tạo bản nháp.
5. Backend kiểm tra cấu trúc, ID, timing, tương thích và chi phí dự trù.
6. Xem khác biệt và tác động; áp dụng các phần đã kiểm tra.
7. Duyệt kế hoạch đúng revision trước các bước sản xuất tương ứng.

AI chỉ đề xuất bản nháp và tuyến sản xuất. AI không tự cấp approval, không tự khóa nội dung, không tự suy đoán Unreal path, bone map hoặc transform. Nội dung kịch bản được xử lý như dữ liệu, không phải lệnh chạy hệ thống.

**Chế độ mô phỏng** dùng dữ liệu xác định để kiểm tra luồng xử lý; nó không gọi mô hình AI thật. **OpenAI API** có thể phát sinh phí và cần key, mô hình được tài khoản hỗ trợ cùng budget grant hợp lệ. Kết quả thực tế cần được đánh giá bằng biên nhận và review; kiểm tra kết nối không xác nhận chất lượng nội dung.

## 4. Phiên bản, khóa và duyệt

Mỗi thay đổi kế hoạch tạo **ShotRevision bất biến**. AssetVersion và ArtifactVersion giữ lịch sử tài nguyên và đầu ra. Approval gắn với phạm vi và phiên bản cụ thể.

Các trục được lưu độc lập:

- **Chế độ:** tự động hoặc thủ công.
- **Khóa:** đã khóa hoặc chưa khóa.
- **Tính hợp lệ:** hiện hành, lỗi thời hoặc bị chặn.
- **Review:** cần duyệt hoặc đã duyệt.

Automation phải kiểm lock và revision khi xếp việc và trước khi ghi. Nội dung thủ công hoặc đã khóa không được tự ghi đè. Chỉnh sửa ngoài Unreal cần được nhận về và đồng bộ, hoặc báo xung đột.

Khi đầu vào đổi, output cũ được giữ và đánh dấu **STALE**. Những bước liên quan cần được xây dựng và duyệt lại theo phiên bản mới. Voice lock phải có trước timing/face; Foley dùng picture revision đã khóa; âm thanh giữ Dialogue, Foley, SFX, Ambience và Music thành stem riêng.

## 5. Kiến trúc

```text
Giao diện React / TypeScript
            ↓ HTTP + sự kiện tiến độ
API Python / FastAPI trên loopback
            ↓
Domain, workflow và quản lý công việc
            ↓
Adapters: Unreal Local Agent / AI provider / media workers
            ↓
Unreal Editor, dịch vụ AI và công cụ bàn giao

SQLite: metadata, phiên bản, approval, jobs, receipts, audit
Filesystem: media, cache, render, audio, delivery và logs
Windows Credential Manager: API key
```

Backend là **modular monolith**: các phần domain và nghiệp vụ được tách khỏi adapters. Công việc dài chạy nền; HTTP không phải chờ toàn bộ thao tác sản xuất hoàn tất. Queue lưu operation ID, input hash, attempt, dependencies và biên nhận để hỗ trợ khôi phục và đối soát.

Yêu cầu có kết quả chưa rõ cần ở trạng thái chờ/đối soát; không tự gửi lại request trả phí chỉ vì timeout. Receipt cần chứng minh output và fingerprint phù hợp; exit code 0 không đủ để xác nhận ghi đúng vào Unreal.

## 6. Trạng thái triển khai thực tế

| Thành phần | Trạng thái và giới hạn |
|---|---|
| Nền tảng local | Có UI, API, migration SQLite, filesystem root, revisions, lock, approval và durable queue. |
| Bộ mô phỏng | Có adapter với nhãn rõ để kiểm tra điều phối. Không tạo bằng chứng tích hợp UE/cloud. |
| Unreal P2 | Có editor Python agent, operation/result schema và queue riêng. Mã ứng viên hướng UE 5.5; chưa có nghiệm thu Sequencer/retarget đầy đủ trong môi trường phát triển này. |
| Giọng và diễn xuất P3 | Có version/review/lock/timing với WAV tổng hợp. Voice provider production, facial solve/bake và ghi máy quay còn thiếu. |
| Cache và render P4 | Có metadata, checksum, frame inventory và guard. Chưa có worker hoàn chỉnh cho simulate/bake/bind/MRQ; final render hiện vẫn bị chặn. |
| Planner P5 | Có bộ mô phỏng và adapter HTTP OpenAI; cấu hình key/model/budget qua UI. Chưa có nghiệm thu chất lượng và chi phí bằng model live trong lượt QA gần nhất. |
| Video/âm thanh P6 | Có hợp đồng, gate và quản lý media. Seedance/Foley production mặc định chưa mở. Package Resolve là prototype, cần kiểm chứng import/relink thực tế. |
| Nghiệm thu P7 | Có hồ sơ, rubric và backup/restore local. Chưa hoàn tất ba pilot thật, khôi phục liên kết UE và rerender; gate phát hành vẫn BLOCKED. |

**BLOCKED** nghĩa là điều kiện cần chưa đủ hoặc chức năng production chưa hoàn tất; không phải PASS. Việc mang ứng dụng sang máy có UE là cần thiết để kiểm tra integration, nhưng không thay thế phần worker còn thiếu.

Bản hiện tại còn lỗi đã tái hiện liên quan tính hiện hành của review, tính đầy đủ của backup, export lặp và thông báo lỗi. UI cũng còn cho duyệt thành phần chưa có output, và có thể hiển thị thư mục backup chưa lưu trong khi job dùng thư mục đã lưu. Cần khắc phục trước khi dùng cho dữ liệu sản xuất quan trọng.

## 7. Chuẩn bị mã nguồn trên Windows

Máy phát triển đã sử dụng Windows 11, Python 3.14 và Node.js 24. Những phiên bản khác chưa được xác nhận trong môi trường này. Unreal không cần thiết để mở UI và sử dụng các luồng local; các bước UE cần bản cài đầy đủ, project cùng asset tương thích.

Repository lưu mã nguồn và dependency lockfiles. Không kèm môi trường Python, node_modules, UI đã build, database, media, test project hoặc báo cáo kiểm thử. Chưa có installer `.exe` tự động; bước dựng source lần đầu dưới đây dành cho người thiết lập/phát triển.

Trong PowerShell ở thư mục repository:

```powershell
py -3.14 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r services/api/requirements.lock.txt
npm ci --prefix apps/web
npm run build --prefix apps/web
```

Sau khi dựng xong:

1. Mở `tools\Create-Studio-Shortcut.cmd` để tạo shortcut ở vị trí hiện tại.
2. Nhấp đúp **Start-Studio.lnk**, hoặc mở trực tiếp `tools\Start-Studio.cmd`.
3. Launcher mở ứng dụng tại `http://127.0.0.1:8000/` trong trình duyệt mặc định.
4. Giữ cửa sổ launcher mở trong lúc dùng; đóng cửa sổ đó để dừng server.

Shortcut sử dụng icon capybara trong `assets/launcher`. File `.lnk` chứa đường dẫn tuyệt đối nên được tạo trên từng máy và không được đưa lên GitHub. Khi chuyển thư mục hoặc máy, tạo lại shortcut. Không copy `.venv` từ máy khác.

Khởi chạy lại khi server Studio đã chạy sẽ mở phiên đang có. Nếu cổng 8000 thuộc ứng dụng khác, launcher báo xung đột. Khi sửa Python cần khởi động lại backend; khi sửa frontend cần build lại giao diện.

## 8. Cấu hình và sử dụng trong app

### Unreal Engine

Vào **Cài đặt**, chọn `UnrealEditor-Cmd.exe` và `.uproject` bằng nút **Duyệt…**. Kiểm tra đường dẫn rồi lưu. Project cần các plugin PythonScriptPlugin, EditorScriptingUtilities và SequencerScripting mà agent sử dụng. Chọn đúng phiên bản và thực hiện trên bản sao project trước.

Trong **Thư viện**, đăng ký đường dẫn asset, anchor và profile từ tài nguyên thật do người vận hành xác nhận. Trong **Công việc → Lắp dựng Unreal**, chọn tập cùng 3–5 cảnh đã duyệt, xem kế hoạch, chạy preflight rồi xếp việc. Nhãn **UNREAL CHƯA CẤU HÌNH** nghĩa là chưa có cấu hình hợp lệ để sử dụng đường Unreal; những công việc mô phỏng vẫn tách riêng.

### OpenAI

Vào **Cài đặt → Dịch vụ AI**:

1. Chọn chế độ OpenAI API và mô hình trong danh mục của app.
2. Chọn **Nhập khóa trong cửa sổ Windows**; nhập key trong hộp thoại native do backend mở.
3. Lưu cấu hình và kiểm tra kết nối nếu cần.
4. Sang **Lập kế hoạch** để cấp hạn mức/thời hạn rồi chủ động yêu cầu tạo nháp.

Frontend chỉ được biết trạng thái có/không có key. Danh mục mô hình và giá tham chiếu có thể cần cập nhật theo nhà cung cấp; app không bảo đảm mọi tài khoản được cấp mọi mô hình. Không tự gọi thêm yêu cầu có phí khi chưa có ngân sách phù hợp.

### Sao lưu

Trong **Nghiệm thu & sao lưu**, chọn thư mục backup và phục hồi, bấm **Lưu thư mục**, xác nhận đã đóng UE/các ứng dụng ghi media rồi xếp sao lưu. Bản phục hồi được tạo ở folder mới; hiện là archive offline, chưa tự đổi dataset đang chạy hoặc kiểm chứng UE/Resolve links.

Do lỗi UI đã biết, cần **lưu lại thư mục sau mỗi thay đổi** và đối chiếu đường dẫn trong kết quả job. Cũng cần kiểm đủ media được DB tham chiếu; trạng thái backup COMPLETED hiện chưa đủ chứng minh bộ dữ liệu không thiếu media.

## 9. Dữ liệu và bảo mật

```text
data/
├── studio.sqlite3
└── projects/<project-id>/
    ├── manifests/
    ├── registry/
    ├── media/
    ├── caches/
    ├── renders/
    ├── audio/
    ├── delivery/
    └── logs/
```

Backend tự áp dụng migration SQLite khi khởi động. Không có down-migration tự động; nâng cấp cần bản sao dữ liệu và phiên bản mã nguồn tương ứng.

API mặc định chỉ lắng nghe loopback. Phiên local, kiểm tra origin và token cho mutation bảo vệ thao tác từ trình duyệt. Không nên mở cổng API ra Internet để biến app thành dịch vụ nhiều người.

API key thuộc kho bảo mật Windows, không nằm trong manifest, SQLite cấu hình, phản hồi frontend hoặc GitHub. Backup ứng dụng không mang theo kho khóa Windows; máy mới cần nhập lại key qua UI. Database, media và cache local được bỏ qua bởi `.gitignore`.

## 10. Cấu trúc repository

| Thư mục/file | Nội dung |
|---|---|
| `apps/web/` | React/TypeScript, giao diện tiếng Việt, theme và cấu hình Vite. |
| `services/api/studio/` | API, domain, workflow, queue, adapters và kho bí mật. |
| `services/api/studio/resources/` | Metadata cần cho giao diện nghiệp vụ khi app chạy, gồm danh mục nhãn nghiệm thu; không phải test runner hoặc kết quả test. |
| `services/api/migrations/` | Migration SQLite. |
| `schemas/` | Schema manifest và hợp đồng dữ liệu. |
| `tools/unreal/` | Unreal editor Python agent. |
| `tools/` | Launcher và công cụ tạo shortcut. |
| `assets/launcher/` | Ảnh và icon launcher. |
| `docs/capability-profile.json` | Profile mà backend đọc; giữ trạng thái chưa kiểm chứng. Tài liệu/evidence được nhắc trong profile không nằm trong repository này. |

Test, fixture, project Unreal mẫu, media chạy thử, báo cáo QA và tài liệu Markdown thiết kế được giữ ở workspace phát triển. **README này là file Markdown duy nhất trong bản đưa lên GitHub.** Mã nguồn chưa được gán giấy phép phân phối riêng.
