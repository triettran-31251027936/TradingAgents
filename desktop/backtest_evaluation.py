"""Read a grid cell's later outcome separately from its original analyst context."""

from __future__ import annotations

from pathlib import Path

from tradingagents.backtest import summarize
from tradingagents.memory import TradingMemoryLog


def section(report, cfg):
    root = Path(cfg["results_dir"]).resolve()
    relative = report.resolve().relative_to(root)
    parts = relative.parts
    if len(parts) != 6 or parts[0] != "backtest" or parts[2] != "evidence":
        return None
    run, ticker, day = parts[1], parts[3], parts[4]
    log = root / "backtest" / run / "trading_memory.md"
    text = (
        "# Đánh giá sau phân tích\n\n"
        "Đây là kết quả và bài học được cập nhật **sau** lượt phân tích, đọc từ log riêng của backtest. "
        "Nội dung này không được đưa ngược vào báo cáo Memory hay context tại ngày phân tích.\n\n"
        f"- Mã: {ticker}\n- Ngày phân tích: {day}\n"
        "- Phạm vi: các ô đánh giá độc lập; lợi nhuận cổ phiếu và alpha không phải lãi/lỗ tài khoản tiền ảo.\n\n"
    )
    status = "unavailable"
    if not log.is_file() or log.is_symlink() or not log.resolve().is_relative_to(root):
        text += "Chưa có log kết quả của lượt backtest này; không suy ra kết quả bằng 0 hoặc tự tạo bài học.\n"
    else:
        matches = [
            e
            for e in TradingMemoryLog({"memory_log_path": str(log)}).load_entries()
            if e["ticker"] == ticker and e["date"] == day
        ]
        if len(matches) != 1:
            text += "Chưa xác định được đúng một quyết định tương ứng trong log. Chưa gắn outcome vào báo cáo này.\n"
        else:
            entry = matches[0]
            decision = report.parent / "5_portfolio/decision.md"
            if (
                not decision.is_file()
                or decision.is_symlink()
                or decision.read_text().strip() != entry["decision"].strip()
            ):
                text += "Quyết định trong log không khớp báo cáo đã chọn. Không gắn kết quả của một quyết định khác vào báo cáo này.\n"
                status = "decision_mismatch"
            else:
                status = "pending" if entry["pending"] else "resolved"
                text += f"- Rating đã ghi: {entry['rating']}\n"
                if entry["pending"]:
                    text += "- Trạng thái: Chờ đủ giá hoặc hoàn tất phản tư. Chưa chấm kết quả.\n"
                else:
                    text += (
                        f"- Trạng thái: Đã ghi kết quả trong log\n- Ngày đủ cửa sổ: {entry.get('resolved') or 'Chưa lưu'}\n"
                        f"- Cửa sổ thực tế: {entry.get('holding') or 'Chưa lưu'}\n"
                        f"- Lợi nhuận chuỗi giá cổ phiếu: {entry.get('raw') or 'Chưa lưu'}\n"
                        f"- Alpha so với benchmark của lượt: {entry.get('alpha') or 'Chưa lưu'}\n"
                        "- Tên benchmark: log Memory gốc không giữ trường này; không suy ra từ cấu hình hiện tại.\n\n"
                        "Các phần trăm trên là số đã làm tròn trong log gốc, không phải độ chính xác của giá nguồn. "
                        "Một cửa sổ ngắn không đủ xác nhận luận điểm nhiều tháng; điều chỉnh cổ tức/quyền và phiên bản dữ liệu cần được đối soát riêng.\n\n"
                    )
                    if entry["rating"] == "Hold":
                        text += "Hold không dự báo chiều giá và không có tỷ lệ đúng chiều.\n\n"
                    elif entry["rating"] == "REVIEW":
                        text += "REVIEW không được chấm thành công/thất bại định hướng.\n\n"
                    text += (
                        "## Phản tư sau kết quả\n\n"
                        + (entry.get("reflection") or "Chưa lưu nội dung phản tư.")
                        + "\n\n"
                    )
                score = summarize(log)
                text += (
                    "## Tổng hợp của lượt backtest\n\n"
                    f"- Đã có kết quả: {score.resolved}\n- Còn chờ: {score.pending}\n- REVIEW không chấm: {score.unscored}\n"
                    f"- Cửa sổ trong log: {score.holding or 'Chưa có'}\n\n"
                )
                for rating, row in score.by_rating.items():
                    direction = (
                        "Không dự báo chiều giá"
                        if row.hit_rate is None
                        else f"Đúng chiều: {row.hit_rate:.1%}"
                    )
                    text += f"- {rating}: n={row.count}; {direction}; alpha bình quân {row.mean_alpha:+.2%}.\n"
                text += "\nĐây là thống kê từ các quyết định của lượt, không phải mô phỏng danh mục, bảo đảm lợi nhuận hay kiểm chứng dự báo trên mẫu lớn.\n"
    return {
        "name": "Đánh giá sau phân tích",
        "group": "8_evaluation",
        "text": text,
        "status": status,
    }
