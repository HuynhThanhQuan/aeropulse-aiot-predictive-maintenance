# Câu hỏi phản biện và khung trả lời

Đọc cùng [hướng dẫn demo](defense-guide-vi.md) và [bảng số liệu](metrics-20261022.md). Trả lời ngắn trước, rồi mở bằng chứng nếu được hỏi sâu.

1. **AI nằm ở đâu, IoT nằm ở đâu?** AI là CNN tại edge service; IoT là telemetry qua broker và luồng command/status quay lại simulator. Hiện chưa nối cảm biến/actuator vật lý.
2. **Có dự đoán RUL không?** Không trực tiếp. RUL dùng tạo nhãn `RUL ≤ 30`; đầu ra là score phân loại nhị phân. Không thể đổi score 0,8 thành còn 20 chu kỳ.
3. **Tại sao lấy 30 chu kỳ và 14 sensor?** Đây là cấu hình của bài thực nghiệm, giữ nhóm sensor được chọn cho FD001. Chưa có ablation đủ để khẳng định 30/14 là tối ưu. Cần khảo sát window/horizon/feature trên validation nếu muốn bảo vệ lựa chọn tối ưu.
4. **Tại sao CNN mà không LSTM?** CNN nhỏ, xử lý mẫu thời gian cục bộ và dễ export/quantize. Chưa benchmark đối chứng nên chỉ nói đáp ứng prototype, không nói vượt trội LSTM.
5. **Chia ngẫu nhiên cửa sổ có sao không?** Cửa sổ chồng lấn cùng engine rất giống nhau, có thể rò rỉ dữ liệu. Dự án chia engine trước rồi mới tạo cửa sổ; scaler chỉ fit trên train.
6. **Validation F1 0,872 có phải kết quả cuối cùng không?** Không. Validation đã dùng chọn checkpoint và ngưỡng nên có thiên lệch lựa chọn. Official test endpoint F1 là 0,80, ngưỡng đã đóng băng.
7. **Vì sao test all-window F1 khoảng 0,705 khác endpoint 0,80?** Đơn vị mẫu khác: 10.196 cửa sổ có tương quan so với một cửa sổ cuối trên mỗi 100 engine. Không chọn metric đẹp hơn mà bỏ điều kiện đánh giá.
8. **Accuracy 91% có đủ tốt cho bảo trì không?** Chưa. Có 7 false negative trên 25 ca nguy cơ thật (recall 72%). Cần đánh giá chi phí bỏ sót/báo giả, khoảng tin cậy và môi trường thật trước ứng dụng.
9. **Ngưỡng 0,64 từ đâu?** Chọn theo F1 validation của model INT8 đang triển khai; không phải xác suất lỗi quy định bởi chuyên gia. Giảm ngưỡng thường tăng recall và báo giả; cần chọn trên validation theo chi phí, không tune bằng test.
10. **INT8 có giảm bốn lần kích thước không?** Không ở model này. File 17,6367 KiB → 13,25 KiB, giảm 24,87%; graph, metadata và thành phần chưa quantize vẫn chiếm chỗ.
11. **INT8 luôn nhanh và ít RAM hơn?** Không. Phụ thuộc kernel, CPU, kích thước model, overhead và tải hệ thống. Lần đo 22/10 INT8 nhanh hơn về median mean latency nhưng RSS sau load hơi cao hơn FP32.
12. **Latency 0,0175 ms có phải trễ dashboard?** Không. Đó là benchmark lời gọi inference batch 1, có warm-up, trên M1 Pro. Không bao gồm MQTT, buffer, InfluxDB, HTTP hay render. Tốc độ nghịch đảo latency không phải throughput toàn hệ thống.
13. **Model nhỏ 13 KiB có chạy trên MCU không?** File nhỏ chưa chứng minh MCU chạy được. Runtime hiện là Python + ONNX Runtime trên CPU laptop/container, dùng hàng trăm MiB RSS. Cần đo trên hardware và runtime đích.
14. **Thêm 6 engine có làm model chính xác hơn không?** Không. Dùng cùng model với 6 buffer, tăng tải xử lý. Demo 095–100 có 95/96/97/99 thuộc train, 98/100 thuộc validation; không coi đây là test độc lập.
15. **Closed loop chứng minh điều gì?** Chứng minh trao đổi alert → command → acknowledgement. Chưa chứng minh bảo trì làm giảm xác suất hỏng vì simulator vẫn phát lại dữ liệu định sẵn.
16. **Reset có làm engine khỏe lại không?** Chỉ xóa cờ bảo trì. Nếu score vẫn cao thì alert tiếp theo có thể kích hoạt lại lệnh. Đồ thị giảm khi replay là bắt đầu quỹ đạo mới.
17. **MQTT QoS 1 có exactly-once không?** Không, at-least-once có thể trùng. Controller có tập active_alerts để hạn chế lặp; trạng thái này nằm RAM. Không tuyên bố xử lý trùng bền vững xuyên restart.
18. **Mất broker hoặc storage thì sao?** Console có cache và báo offline/stale; Fleet phụ thuộc InfluxDB. Nếu storage ngừng ghi, engine thành Unknown sau ngưỡng freshness. Cache không thay thế message queue bền vững hoặc database.
19. **Reporting 100% có nghĩa hệ thống hoàn hảo?** Chỉ engine thấy trong lookback đều có prediction mới. Chưa có registry cố định nên thiết bị chưa từng gửi dữ liệu có thể không nằm mẫu số.
20. **Risk exposure 50% là gì?** Một nửa số engine đang reporting vượt ngưỡng; không phải xác suất đội máy hỏng 50% và không phải lỗi model 50%.
21. **Score 80% có được calibration chưa?** Chưa có kiểm chứng reliability/calibration; nên gọi là score/xác suất do model ước lượng, không cam kết 80 trong 100 trường hợp thực sẽ hỏng.
22. **Dùng được cho động cơ thực ngay không?** Chưa: FD001 mô phỏng, ít điều kiện vận hành, chưa kiểm chứng drift, nhiễu/mất sensor, lỗi mạng, chi phí quyết định và an toàn cơ khí.
23. **Bảo mật hiện thế nào?** Bản demo bind các cổng publish vào localhost; MQTT vẫn anonymous trong mạng Compose và dashboard chưa có tài khoản. Không đưa thẳng ra Internet; triển khai thật cần xác thực/phân quyền/TLS và quản lý secret.
24. **Bản Xuân khác bản Quan ở đâu?** Thêm Fleet API/UI, cache offline, template Influx và sáu simulator; ZIP thiếu query Flux đã được bổ sung. Bộ model triển khai giữ của Quan để nhất quán bằng chứng 0,64; bản Xuân 0,62 lưu riêng.
25. **AI assistant tham gia phần nào?** Hỗ trợ đọc code, tích hợp, debug, kiểm tra, tài liệu và bằng chứng. Nhóm chịu trách nhiệm hiểu, chạy lại và xác nhận; không coi nội dung do AI viết là kết quả thực nghiệm nếu chưa đo.

## Tự vấn đáp trước buổi thi

Không nhìn tài liệu, hãy: vẽ 6 service chính; kể 5 loại topic; tính precision/recall từ TP18 FP2 FN7; giải thích vì sao FN quan trọng; tính exposure khi 2/5 engine vượt ngưỡng; chỉ ra file threshold; nêu ba giới hạn còn lại.
