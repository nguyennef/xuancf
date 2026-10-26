import subprocess
import sys
import os
import datetime
import zipfile
import re

# Đảm bảo hiển thị tiếng Việt UTF-8 chuẩn trên Windows Console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')
    except Exception:
        pass

def run_cmd(cmd):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, encoding='utf-8', errors='ignore')

def clean_branch_name(name):
    vietnamese_map = {
        'a': 'áàảãạăắằẳẵặâấầẩẫậ',
        'd': 'đ',
        'e': 'éèẻẽẹêếềểễệ',
        'i': 'íìỉĩị',
        'o': 'óòỏõọôốồổỗộơớờởỡợ',
        'u': 'úùủũụưứừửữự',
        'y': 'ýỳỷỹỵ'
    }
    cleaned = name.lower()
    for non_accent, accents in vietnamese_map.items():
        for char in accents:
            cleaned = cleaned.replace(char, non_accent)
    # Thay thế khoảng trắng hoặc ký tự đặc biệt bằng dấu gạch ngang
    cleaned = re.sub(r'[\s_]+', '-', cleaned.strip())
    cleaned = re.sub(r'[^a-z0-9\-.]', '', cleaned)
    cleaned = re.sub(r'^-+|-+$', '', cleaned)
    return cleaned or 'update'

def main():
    print("=" * 60)
    print("     TIỆM CÀ PHÊ XUÂN - CÔNG CỤ TỰ ĐỘNG ĐẨY CODE LÊN GITHUB")
    print("=" * 60)
    print()

    # Kiểm tra trạng thái Git
    res = run_cmd("git status --porcelain")
    uncommitted = res.stdout.strip()

    # Kiểm tra xem có commit nào chưa push không
    ahead_res = run_cmd("git rev-list @{u}..HEAD")
    unpushed_commits = [c for c in ahead_res.stdout.splitlines() if c.strip()]

    if not uncommitted and not unpushed_commits:
        print("[THÔNG BÁO] Không có file nào thay đổi so với bản trên Git!")
        print("Tất cả code hiện tại đã được đồng bộ với GitHub.")
        return

    if uncommitted:
        lines = [line for line in uncommitted.splitlines() if line.strip()]
        print("[DANH SÁCH FILE VỪA CHỈNH SỬA]:")
        for line in lines:
            print(f"  * {line}")
        print()
    elif unpushed_commits:
        print(f"[THÔNG BÁO] Bạn đang có {len(unpushed_commits)} commit đã lưu ở máy và chuẩn bị đẩy lên GitHub!")
        print()

    print("=" * 60)
    print("  1. Đẩy lên NHÁNH RIÊNG trên GitHub (Mặc định: nhánh 'update')")
    print("  2. Đẩy thẳng lên NHÁNH CHÍNH (nhánh 'main')")
    print("  3. Vừa đẩy lên Git VỪA nén các file sửa thành update.zip")
    print("=" * 60)

    try:
        choice = input("Chọn thao tác [1, 2 hoặc 3] (Nhấn Enter mặc định chọn 1): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nĐã hủy.")
        return

    if not choice:
        choice = "1"

    branch = "update"
    if choice == "2":
        branch = "main"
    else:
        try:
            raw_branch = input("Nhập tên nhánh riêng (VD: yeu-thich hoặc bấm Enter để dùng 'update'): ").strip()
            if raw_branch:
                branch = clean_branch_name(raw_branch)
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy.")
            return

    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    default_msg = f"Cap nhat code ngay {now_str}"

    commit_msg = default_msg
    if uncommitted:
        try:
            user_msg = input(f"Nhập ghi chú cập nhật (Nhấn Enter để dùng: '{default_msg}'): ").strip()
            if user_msg:
                commit_msg = user_msg
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy.")
            return

    print()
    print(f"[1/3] Đang chuyển sang nhánh '{branch}'...")
    checkout_res = subprocess.run(f'git checkout -B "{branch}"', shell=True)
    if checkout_res.returncode != 0:
        print(f"[!] Lỗi khi chuyển sang nhánh '{branch}'!")
        return

    if uncommitted:
        print("[2/3] Đang ghi nhận các file thay đổi (Database đã tự động loại bỏ)...")
        subprocess.run("git add .", shell=True)
        subprocess.run(f'git commit -m "{commit_msg}"', shell=True)
    else:
        print("[2/3] Các thay đổi đã được commit sẵn sàng.")

    print(f"[3/3] Đang đẩy (push) lên GitHub nhánh '{branch}'...")
    push_res = subprocess.run(f'git push -u origin "{branch}"', shell=True)

    if push_res.returncode != 0:
        print()
        print("=" * 60)
        print(f"[!] ĐẨY LÊN GITHUB THẤT BẠI. Vui lòng kiểm tra lại kết nối mạng hoặc quyền truy cập GitHub!")
        print("=" * 60)
        return

    if choice == "3":
        print()
        print("[*] Đang đóng gói các file vừa sửa thành update.zip...")
        try:
            diff_res = run_cmd(f'git diff --name-only main..."{branch}"')
            files_to_zip = [f.strip() for f in diff_res.stdout.splitlines() if f.strip() and os.path.isfile(f.strip())]
            if not files_to_zip:
                diff_head = run_cmd("git diff-tree --no-commit-id --name-only -r HEAD")
                files_to_zip = [f.strip() for f in diff_head.stdout.splitlines() if f.strip() and os.path.isfile(f.strip())]

            # Bỏ qua database và zip
            files_to_zip = [f for f in files_to_zip if not f.endswith('.db') and not f.endswith('.zip') and not f.endswith('.bat') and not f.endswith('.py')]

            with zipfile.ZipFile("update.zip", "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files_to_zip:
                    zf.write(f)
            print(f"-> Đã nén thành công {len(files_to_zip)} file vào 'update.zip' trong thư mục quán!")
        except Exception as e:
            print(f"[!] Lỗi khi nén file zip: {e}")

    print()
    print("=" * 60)
    print(f"  HOÀN TẤT! Đã đẩy code lên nhánh '{branch}' thành công.")
    print(f"  Xem trên GitHub: https://github.com/nguyennef/xuancf/tree/{branch}")
    print("=" * 60)
    print()

if __name__ == "__main__":
    main()
