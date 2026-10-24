# Đọc source AeroPulse và hiểu các điểm tune ONNX

Tài liệu mô tả code hiện tại. Các thử nghiệm tune bên dưới là đề xuất, chưa chạy và chưa thay đổi model demo.

## Đi theo đường đi của dữ liệu

1. `src/aiot_pm/data.py`: đọc FD001, chia ID engine thành train/validation trước khi tạo cửa sổ; fit StandardScaler chỉ trên train. Mỗi cửa sổ có dạng `[14 sensor, 30 cycle]`, nhãn là `RUL ở cuối cửa sổ <= 30`.
2. `src/aiot_pm/model.py`: TinyConvNet nhận `[batch,14,30]`. Conv → BatchNorm → ReLU → MaxPool → Conv → BatchNorm → ReLU → Global Average Pool → Linear. Đầu ra là một **logit phân loại**, không phải số chu kỳ RUL.
3. `src/aiot_pm/train.py`: học trọng số bằng PyTorch, chọn checkpoint trên validation, xuất FP32 ONNX, lượng tử hóa INT8 rồi chọn lại threshold trên validation của INT8.
4. `src/aiot_pm/simulator.py`: phát từng dòng CSV qua MQTT; nhận command và trả status xác nhận.
5. `src/aiot_pm/preprocessing.py` và `edge_service.py`: gom cửa sổ riêng mỗi engine, dùng đúng thứ tự sensor và scaler đã lưu, chạy ONNX Runtime, sigmoid rồi so threshold. Phát prediction và alert khi high, có cooldown.
6. `controller_service.py`: alert → command bảo trì; chặn lệnh lặp trong phiên chạy. Đây là điều khiển mô phỏng bằng cờ trạng thái.
7. `storage_service.py`: ghi telemetry/prediction/alert/status vào InfluxDB theo lô. `dashboard.py` nhận MQTT và dùng SQLite cache cho engine console; `fleet.py` đọc InfluxDB qua Flux cho fleet dashboard. Hai đường dữ liệu có thể khác độ trễ.
8. `benchmark.py`: so FP32/INT8 trên các cửa sổ validation đã lưu; đo batch=1 sau warm-up. Đây không phải đánh giá official test.

## ONNX đang làm gì?

ONNX là định dạng graph và trọng số. PyTorch thực hiện việc học, còn ONNX Runtime thực hiện suy luận. Export ONNX không tự học thêm và không tự tăng accuracy.

Trong `export_models()`:

- `torch.onnx.export`: xuất graph FP32. `sensor_window` là input; `failure_logit` là output. Chỉ trục batch động; đổi số sensor hoặc chiều dài cửa sổ cần export lại.
- `opset_version=17`: phiên bản các toán tử ONNX, không phải mức tối ưu.
- `quant_pre_process`: suy luận shape và tối ưu graph trước lượng tử hóa.
- `ArrayCalibrationReader`: đưa từng cửa sổ train đã chuẩn hóa vào bước calibration. Tối đa 512 cửa sổ được lấy rải đều theo chỉ số, chưa phân tầng theo engine hay mức rủi ro.
- `quantize_static`: post-training quantization (PTQ), không backprop. Calibration đo phạm vi activation để xác định scale/zero-point. Trực giác: `q ≈ round(x / scale) + zero_point`, và `x ≈ scale × (q − zero_point)`.
- `QuantFormat.QDQ`: chèn các nút QuantizeLinear/DequantizeLinear; activation dùng QUInt8, weight dùng QInt8, `per_channel=True` dùng scale riêng theo kênh trọng số. Không có nghĩa mọi toán tử đều chạy INT8; điều này còn phụ thuộc graph và kernel của runtime.
- Input vẫn `float32 [batch,14,30]`. Không đổi dữ liệu đầu vào thành `int8` thủ công.
- Cuối cùng tính score INT8 trên validation và chọn threshold. Model, thứ tự sensor, scaler và threshold trong `metadata.json` phải được triển khai cùng nhau.

## Tune ở đâu?

| Mục tiêu | Vị trí | Hiện tại | Khi thử thay đổi |
|---|---|---|---|
| Học tốt hơn | `config/config.yaml` → `training` | lr 0.001; batch 128; tối đa 12 epoch; patience 4 | Train lại, chọn bằng validation, export lại |
| Thay kiến trúc | `model.py` | Conv 24/32 kênh; kernel 5/3 | Train và export lại; đo cả chất lượng, kích thước, latency |
| Thay khoảng quan sát | `config/config.yaml` → `data.window_size` | 30 cycle | Tạo lại cửa sổ, train, export và metadata |
| Định nghĩa cảnh báo | `data.warning_rul` | RUL <= 30 | Đây là thay đổi nhãn/bài toán, không chỉ chỉnh độ nhạy |
| Chất lượng calibration | `training.calibration_samples` và chọn `indexes` trong `export_models()` | Tối đa 512 mẫu train rải đều | Thử độ đại diện và số mẫu; giữ nguyên checkpoint để so PTQ công bằng |
| Định dạng lượng tử | `export_models()` | QDQ; QUInt8/QInt8; per-channel | Các lựa chọn hiện hard-code; cần sửa code và kiểm tra tương thích runtime |
| Bỏ sót so với báo nhầm | `choose_threshold()` | Quét 0.10–0.90, bước 0.01, tối đa F1 | Có thể đổi sang F2 hoặc tối đa recall với precision tối thiểu đã thống nhất |
| Tốc độ CPU | Tạo `InferenceSession` trong `train.py`, `benchmark.py`, `edge_service.py` | CPUExecutionProvider, session options mặc định | Nếu thử thread/session options, đo trên máy đích và thống nhất cấu hình benchmark/deploy |

Không coi 0.64 là hằng số cần tune quanh nó: code chọn threshold từ score validation của artifact được tạo ra. `np.argmax` chọn ngưỡng thấp nhất khi nhiều ngưỡng có cùng F1. Hạ threshold trên cùng một bộ score sẽ không giảm recall, nhưng có thể tăng false positive. F2 nhấn mạnh recall hơn F1; không bảo đảm đạt mọi mức recall mong muốn.

`alert_cooldown_seconds` chỉ hạn chế alert lặp. Dải watch `max(0.25, threshold * 0.70)` là quy tắc hiển thị. Hai tham số này không học lại mô hình và không cải thiện recall của bộ phân loại.

## Quy trình thử nghiệm không làm mất bản demo

1. Tạo một bản sao dự án riêng. Giữ bộ model, metadata, config và kết quả hiện có làm baseline. `make train` ghi đè artifact trong thư mục cấu hình; không chạy trực tiếp trên bản demo để thử tùy ý.
2. Giữ nguyên seed/split train-validation khi so các cấu hình. Chỉ thay một nhóm tham số mỗi lượt; ghi lại cấu hình và tiêu chí chọn trước khi xem test.
3. Nếu tune mạng, train lại trong bản sao. Nếu chỉ tune PTQ, dùng lại cùng checkpoint và scaler rồi gọi riêng `export_models()` với dữ liệu tương ứng; luồng `main()` hiện luôn train trước export, chưa có CLI chỉ quantize.
4. Calibration chỉ lấy từ train. So FP32 và INT8 trên cùng validation: precision, recall, F1, confusion matrix và sự thay đổi score. Có thể chọn threshold riêng cho artifact triển khai; ghi rõ khi so ở chung threshold hay threshold riêng.
5. Với mục tiêu ít bỏ sót, chọn F2 hoặc ràng buộc precision trên validation. Đừng chỉnh threshold theo vài engine đang trình diễn.
6. Đo latency mean/P95, kích thước và RSS trên máy sẽ demo. INT8 có overhead QDQ nên file nhỏ hơn chưa chắc nhanh hơn; latency `session.run` không phải độ trễ toàn hệ thống.
7. Khóa cấu hình và threshold rồi mới đánh giá official test. Báo riêng cửa sổ cuối của 100 engine và toàn bộ cửa sổ; không trộn hai cách đếm thành một kết quả.
8. Chỉ thay bản demo khi đã chấp nhận kết quả; triển khai đồng bộ ONNX + metadata, khởi động lại service để nạp artifact mới.

## Cách giải thích khi trình bày

“Nhóm huấn luyện CNN bằng PyTorch rồi xuất graph sang ONNX. Sau đó dùng dữ liệu train để calibration và lượng tử hóa tĩnh INT8 theo QDQ. Vì lượng tử hóa có thể làm thay đổi score, nhóm chọn ngưỡng trên validation của bản INT8. Edge dùng ONNX Runtime trên CPU, nhận cửa sổ đã chuẩn hóa và trả logit; sigmoid cùng threshold tạo quyết định cảnh báo. Model không dự đoán trực tiếp số chu kỳ RUL.”
