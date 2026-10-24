# Đối chiếu test replay với RUL thật — 24/10/2026

Đánh giá lại model INT8 đang triển khai, ngưỡng cố định 0.64. Dùng scaler và cửa sổ 30 chu kỳ trong metadata; không train lại, không chỉnh ngưỡng. Số dưới đây được chạy lại bằng ONNX Runtime trên host, dùng cùng phép chuẩn hóa float32 với Edge.

`RUL(t) = chu kỳ cuối được quan sát − t + RUL_FD001[engine]`. Nhãn cảnh báo thật là `RUL ≤ 30`. RUL chỉ dùng kiểm tra offline, không truyền vào model.

| Test engine | Chu kỳ cuối | RUL cuối | Điểm dự đoán cuối | Kết quả cuối |
| --- | ---: | ---: | ---: | --- |
| 001 | 31 | 112 | 1.49% | Không cảnh báo, đúng nhãn |
| 002 | 49 | 98 | 2.68% | Không cảnh báo, đúng nhãn |
| 003 | 126 | 69 | 35.46% | Không cảnh báo, đúng nhãn |
| 004 | 106 | 82 | 1.81% | Không cảnh báo, đúng nhãn |
| 005 | 98 | 91 | 1.30% | Không cảnh báo, đúng nhãn |
| 006 | 105 | 93 | 1.07% | Không cảnh báo, đúng nhãn |

Cả sáu engine đều có RUL > 30 trong toàn bộ đoạn test được cung cấp; không có cửa sổ dương tính nào để phát hiện. Vì thế chúng không tạo cảnh báo High/Critical. TEST-003 có điểm tối đa 48.34%, đôi lúc vào Watch/Warning (ngưỡng 44.8%), nhưng vẫn dưới ngưỡng cảnh báo 64%. Normal là trạng thái theo điểm model tại thời điểm đó, không có nghĩa động cơ không bao giờ hỏng.

## Thay TEST-001 bằng TEST-034

- Chọn theo RUL cuối nhỏ nhất trong 100 engine test: 7 chu kỳ. Đây là lựa chọn có chủ đích để minh họa ca gần hỏng, không phải mẫu đánh giá đại diện.
- TEST-034 kết thúc ở chu kỳ 203; RUL bằng 7, chưa quan sát được thời điểm hỏng thực sự.
- Bắt đầu thuộc nhãn dương tính ở chu kỳ 180 (RUL 30). Model lần đầu cảnh báo ở chu kỳ 188 (RUL 22), trễ 8 chu kỳ.
- Có 24 cửa sổ dương tính: model bỏ sót 8 cửa sổ đầu, phát hiện 16 cửa sổ còn lại; không cảnh báo nhầm trong phần trước đó.
- Dự đoán cuối: 99.55%, vượt ngưỡng 64%. Trong replay, phải đợi đến đoạn cuối mới thấy Critical; ở tốc độ 0.25 giây/chu kỳ, khoảng 47 giây sau khi bắt đầu một vòng.
- Khi replay về đầu, buffer và biểu đồ bắt đầu lại. Cờ bảo trì có thể còn giữ; nó không biểu diễn một lần sửa chữa thật.

## Giới hạn đánh giá

Trong kiểm tra endpoint toàn bộ 100 engine, vẫn có 7 ca dương tính bị bỏ sót: 018, 037, 040, 052, 053, 061, 090. Không suy ra accuracy từ 6 engine được chọn để demo. Giữ báo cáo đánh giá chính thức và các phạm vi endpoint/all-windows tách biệt.

TEST-001 đã dừng phát; dữ liệu lịch sử vẫn được giữ. Hai dashboard ẩn engine này khỏi danh sách/tổng số qua `DASHBOARD_EXCLUDED_ENGINES`. Sáu test đang chạy là 002–006 và 034.

[Kết quả từng engine và hash model](reference-results/2026-10-24-test-replay.json).

## Kiểm chứng live sau thay engine

Đã nhận dự đoán High đầu tiên tại chu kỳ 188 và dự đoán cuối tại chu kỳ 203 là 0.9954627573 (99.55%), khớp kiểm tra offline. Đã quan sát đủ alert → command tự động → status xác nhận `maintenance_scheduled`. Fleet còn đúng 12 engine, TEST-034 có nhãn Test và TEST-001 không còn trong danh sách demo. 27 test và Ruff đạt; model giữ nguyên hash.
