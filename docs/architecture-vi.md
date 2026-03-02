# Tài liệu Kiến trúc

## 1. Vấn đề cần giải quyết

Đề tài giải quyết bài toán **tự động hóa việc trích xuất thông tin từ file nhị phân** trong quá trình reverse engineering. Nếu làm thủ công bằng IDA hoặc Ghidra, người phân tích phải mở từng binary, chờ auto-analysis rồi lần lượt xuất functions, strings, imports/exports, pseudocode, disassembly và call graph. Cách làm này tốn thời gian, khó lặp lại và không thuận tiện khi xử lý nhiều file.

Bên cạnh đó, output dump thường lớn và không đồng nhất giữa các công cụ, gây khó khăn cho lưu trữ, chia sẻ hoặc dùng tiếp cho AI, báo cáo và hậu xử lý. Vì vậy, hệ thống được xây dựng như một lớp điều phối trung gian giúp chạy phân tích headless, chuẩn hóa đầu ra và quản lý artifact một cách nhất quán.

## 2. Tại sao chọn tech stack / công cụ này

**Python CLI** được chọn làm thành phần điều phối vì dễ viết script tự động hóa, mạnh về xử lý file, gọi tiến trình ngoài, ghi log và mở rộng chức năng. Python cũng phù hợp với workflow của reverse engineering.

**IDA Pro + IDAPython** được dùng làm backend thứ nhất vì có chất lượng disassembly và decompile tốt, phù hợp khi cần pseudocode chi tiết, danh sách hàm, imports/exports, strings và call graph.

**Ghidra Headless + Java Script** được dùng làm backend thứ hai vì miễn phí, phổ biến, hỗ trợ chạy không cần giao diện và phù hợp cho môi trường automation. Việc hỗ trợ cả **IDA và Ghidra** giúp hệ thống linh hoạt hơn: có thể chọn công cụ theo môi trường sẵn có hoặc chuyển backend khi một tool gặp lỗi.

## 3. Luồng hoạt động chính

```text
Người dùng
  -> chạy cli.py và chọn backend (IDA hoặc Ghidra)
  -> CLI kiểm tra tham số và auto-detect đường dẫn công cụ
  -> nạp một hoặc nhiều file binary
  -> gọi script dump tương ứng
       IDA    -> ida_dump.py
       Ghidra -> DisableWinResRef.java + GhidraDump.java
  -> backend thực hiện auto-analysis và trích xuất
       functions, strings, imports, exports,
       pseudocode, disassembly, call graph
  -> CLI chuẩn hóa output, ghi log, kiểm tra kích thước file
  -> nếu output lớn thì split hoặc zip
  -> lưu kết quả vào thư mục output
```

## 4. Kiến trúc tổng quát

Hệ thống gồm 3 lớp chính:

- **CLI Orchestrator**: nhận input, chọn backend, quản lý runtime, log và output.
- **Backend Adapter**: kết nối từ CLI sang IDA hoặc Ghidra headless.
- **Dump Engine**: thực hiện trích xuất thông tin từ binary và xuất artifact dạng text có cấu trúc.

## 5. Kết luận

Kiến trúc này giúp quá trình reverse engineering **nhanh hơn, dễ lặp lại, dễ mở rộng và thuận tiện tích hợp vào pipeline phân tích tự động**. Đây là giải pháp phù hợp cho malware analysis, crackme và binary triage khi cần xử lý nhiều file một cách thống nhất.
