# Dashboard vẫn mở khi MQTT tắt

Trang web không nằm trong pipeline cảm biến. Nó chỉ xem dữ liệu. Vì vậy trang vẫn phải mở được khi Docker hoặc Mosquitto chưa chạy.

## Ba trạng thái trên huy hiệu

- LIVE, màu xanh: đã nối MQTT và có tin mới trong vòng 5 giây.
- STALE, màu vàng: vẫn nối MQTT nhưng chưa có tin mới. Chữ trên huy hiệu là `no new data for N s`.
- OFFLINE, màu đỏ: chưa nối được MQTT. Trang hiện dữ liệu đã lưu, kèm giờ cập nhật cuối.

Ngưỡng 5 giây đổi bằng biến `DASHBOARD_STALE_AFTER_S`.

Sau tích hợp 22/10, freshness của snapshot được tính riêng theo engine đang chọn; engine khác gửi tin không làm engine đã dừng trở thành LIVE. Health toàn dashboard vẫn mô tả kết nối chung. Trong Compose, `data/dashboard` được mount vào host để cache tồn tại khi container được tạo lại. Cache chỉ hỗ trợ engine console; Fleet vẫn truy vấn InfluxDB.

## Dữ liệu được giữ ở đâu

Mỗi tin telemetry, prediction, alert, status và command được xếp vào một hàng đợi. Một luồng nền ghi hàng đợi đó vào SQLite, mặc định là `data/dashboard/cache.sqlite`. File này không được commit.

Ghi đĩa không chạy trong callback MQTT. Luồng nền ghi khi đủ 50 dòng hoặc sau 1 giây. Mỗi phút nó xóa bớt, chỉ giữ 2000 dòng mới nhất cho mỗi động cơ và mỗi loại tin.

Khi dashboard khởi động lại, nó đọc tối đa 180 điểm telemetry, 180 điểm prediction, 40 sự kiện và trạng thái mới nhất. Biểu đồ có số liệu trước cả khi MQTT nối lại.

`/api/health` luôn trả HTTP 200. `status` là `ok` khi đang LIVE, và `degraded` khi STALE hoặc OFFLINE.

Nút Schedule và Reset bị tắt khi OFFLINE. Gọi `POST /api/command` lúc đó trả 503 với `{"detail":"MQTT not connected"}`.

## Vì sao không xóa lịch sử khi vòng lặp phát lại

Bản tin QoS 1 có thể được gửi lại đúng một chu kỳ. Nếu xóa biểu đồ mỗi lần chu kỳ không tăng, một bản tin trùng sẽ làm mất cả đồ thị.

Bộ nhớ chỉ bị xóa khi chu kỳ mới nhỏ hơn chu kỳ cuối quá 5 đơn vị. Đó là dấu hiệu vòng phát lại bắt đầu lại từ đầu. Các dòng đã ghi trong SQLite không bị xóa theo sự kiện này.

## Khi trình duyệt không gọi được chính dashboard

Biểu đồ đang vẽ được giữ nguyên. Trang hiện `Cannot reach dashboard server` và thử lại, khoảng cách tăng dần đến tối đa 10 giây.
