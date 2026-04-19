import random
from pathlib import Path
from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials
from PIL import Image

# ============================================================
# 基础配置
# ============================================================
IMAGE_DIR = "3band"
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
LABELS = ["inner", "outer", "nuclear", "unclear", "not_ring", "skip"]
GSHEET_ID = "1MAKgvgP0vFVTPpjLWmWhDKODEZHkP1ZRk7uVipAYsIs"

# ============================================================
# 页面设置
# ============================================================
st.set_page_config(page_title="Ring Galaxy Classifier", layout="wide")
st.title("Ring Galaxy Classifier")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 1rem;
        max-width: 96rem;
    }
    div[data-testid="stImage"] img {
        max-height: 68vh;
        object-fit: contain;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# Google Sheets
# ============================================================
@st.cache_resource
def init_gsheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        st.secrets["gcp_service_account"],
        scope,
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GSHEET_ID).sheet1
    return sheet


@st.cache_data(ttl=60, show_spinner=False)
def fetch_user_records_from_gsheet(user_name: str):
    """
    返回当前用户的所有记录，并额外带上该记录在 Google Sheet 中的真实行号 sheet_row
    方便后续 update 覆盖，而不是 append 新行。
    """
    try:
        sheet = init_gsheet()
        all_values = sheet.get_all_values()
        if not all_values:
            return []

        header = all_values[0]
        records = []

        for row_idx, row in enumerate(all_values[1:], start=2):  # Google Sheet 真正行号从2开始
            padded_row = row + [""] * (len(header) - len(row))
            record = dict(zip(header, padded_row))

            if str(record.get("user_name", "")).strip() == user_name:
                record["sheet_row"] = row_idx
                records.append(record)

        return records
    except Exception:
        return []


def build_marked_history(user_records):
    """
    根据当前用户记录构建“标注历史”：
    - 同一张图只保留一次
    - 按 timestamp 从早到晚排序
    - 若 timestamp 解析失败，则尽量放后面
    """
    latest_map = {}

    for r in user_records:
        image_name = str(r.get("image_name", "")).strip()
        ts = str(r.get("timestamp", "")).strip()

        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = datetime.min

        if image_name:
            latest_map[image_name] = (dt, r)

    ordered = sorted(latest_map.items(), key=lambda x: x[1][0])
    return [img_name for img_name, _ in ordered]


def load_user_state(user_name: str):
    user_records = fetch_user_records_from_gsheet(user_name)
    user_done_names = {str(r.get("image_name", "")).strip() for r in user_records}

    # 同一张图如果有多条，默认以后出现的覆盖前面的
    user_record_map = {
        str(r.get("image_name", "")).strip(): r
        for r in user_records
    }

    st.session_state.user_records = user_records
    st.session_state.user_done_names = user_done_names
    st.session_state.user_record_map = user_record_map
    st.session_state.marked_history = build_marked_history(user_records)


def move_image_to_history_end(image_name: str):
    """
    把刚标注/刚修改的图片放到用户历史末尾，表示“最近一次标注”
    """
    history = st.session_state.get("marked_history", [])
    history = [x for x in history if x != image_name]
    history.append(image_name)
    st.session_state.marked_history = history


def save_annotation(user_name: str, image_path: str, label: str, comment: str):
    """
    同一用户 + 同一图片：
    - 如果已有记录 -> 更新原行
    - 如果没有记录 -> append 新行
    """
    sheet = init_gsheet()
    image_name = Path(image_path).name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing = st.session_state.user_record_map.get(image_name)

    if existing and existing.get("sheet_row"):
        sheet_row = int(existing["sheet_row"])
        new_row = [user_name, image_name, label, comment, now]
        sheet.update(f"A{sheet_row}:E{sheet_row}", [new_row])

        updated_record = {
            "user_name": user_name,
            "image_name": image_name,
            "label": label,
            "comment": comment,
            "timestamp": now,
            "sheet_row": sheet_row,
        }

        # 更新 user_records 中对应那一条
        for i, r in enumerate(st.session_state.user_records):
            if int(r.get("sheet_row", -1)) == sheet_row:
                st.session_state.user_records[i] = updated_record
                break

        st.session_state.user_record_map[image_name] = updated_record
        st.session_state.user_done_names.add(image_name)
        move_image_to_history_end(image_name)

    else:
        row = [user_name, image_name, label, comment, now]
        sheet.append_row(row)

        # append 后重新拉一次用户数据，拿到新行号，最稳
        fetch_user_records_from_gsheet.clear()
        refreshed_records = fetch_user_records_from_gsheet(user_name)

        user_records = refreshed_records
        user_done_names = {str(r.get("image_name", "")).strip() for r in user_records}
        user_record_map = {
            str(r.get("image_name", "")).strip(): r
            for r in user_records
        }

        st.session_state.user_records = user_records
        st.session_state.user_done_names = user_done_names
        st.session_state.user_record_map = user_record_map
        move_image_to_history_end(image_name)

    fetch_user_records_from_gsheet.clear()

# ============================================================
# 图片列表
# ============================================================
def load_images():
    image_dir = Path(IMAGE_DIR)
    if not image_dir.exists():
        return []

    files = []
    for p in image_dir.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
            files.append(p)

    files.sort(key=lambda x: x.name)
    return files


def get_random_unlabeled_index(images, done_names):
    candidates = [i for i, p in enumerate(images) if p.name not in done_names]
    if not candidates:
        return 0 if images else None
    return random.choice(candidates)


def get_index_by_exact_image_number(images, image_number: int):
    """
    输入 123 -> 找文件名 stem 恰好等于 '123' 的图片，如 123.png / 123.jpg
    """
    target = str(image_number)
    for i, p in enumerate(images):
        if p.stem == target:
            return i
    return None


def get_previous_marked_index(images, current_image_name, marked_history):
    """
    上一张 = 跳到“用户标注历史中的上一张”
    规则：
    - 如果当前图片不在历史里 -> 跳到最后一张已标注图片
    - 如果当前图片在历史里且不是第一张 -> 跳到历史中的前一张
    - 如果当前图片已经是历史第一张 -> 仍停在第一张
    """
    if not marked_history:
        return None

    name_to_index = {p.name: i for i, p in enumerate(images)}

    if current_image_name not in marked_history:
        target_name = marked_history[-1]
        return name_to_index.get(target_name)

    pos = marked_history.index(current_image_name)
    if pos <= 0:
        target_name = marked_history[0]
    else:
        target_name = marked_history[pos - 1]

    return name_to_index.get(target_name)

# ============================================================
# 初始化状态
# ============================================================
images = load_images()

if "user_name" not in st.session_state:
    st.session_state.user_name = ""
if "last_user_name" not in st.session_state:
    st.session_state.last_user_name = ""
if "current_index" not in st.session_state:
    st.session_state.current_index = 0
if "last_saved_message" not in st.session_state:
    st.session_state.last_saved_message = ""
if "user_records" not in st.session_state:
    st.session_state.user_records = []
if "user_done_names" not in st.session_state:
    st.session_state.user_done_names = set()
if "user_record_map" not in st.session_state:
    st.session_state.user_record_map = {}
if "marked_history" not in st.session_state:
    st.session_state.marked_history = []
if "comment_value" not in st.session_state:
    st.session_state.comment_value = ""
if "pending_comment_reset" not in st.session_state:
    st.session_state.pending_comment_reset = False

# ============================================================
# 侧边栏：用户
# ============================================================
st.sidebar.header("User")
user_name = st.sidebar.text_input("请输入用户名", value=st.session_state.user_name).strip()

if user_name:
    if user_name != st.session_state.last_user_name:
        st.session_state.user_name = user_name
        st.session_state.last_user_name = user_name
        load_user_state(user_name)
        next_idx = get_random_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
        st.session_state.pending_comment_reset = True
        st.rerun()
    else:
        st.session_state.user_name = user_name

if not st.session_state.user_name:
    st.info("请先在左侧输入用户名。")
    st.stop()

# ============================================================
# 分类标准（PDF）
# ============================================================
st.sidebar.markdown("---")
st.sidebar.subheader("分类标准")

PDF_PATH = "criteria.pdf"   # 改成你的 PDF 文件名

show_pdf = st.sidebar.checkbox("打开分类标准", value=False)

if show_pdf:
    if Path(PDF_PATH).exists():
        import base64

        with open(PDF_PATH, "rb") as f:
            pdf_bytes = f.read()

        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        pdf_display = f"""
        <iframe
            src="data:application/pdf;base64,{base64_pdf}"
            width="100%"
            height="700px"
            type="application/pdf"
            style="border: 1px solid #ddd; border-radius: 8px;"
        ></iframe>
        """
        st.sidebar.markdown(pdf_display, unsafe_allow_html=True)
    else:
        st.sidebar.warning(f"未找到 PDF 文件：{PDF_PATH}")

user_name = st.session_state.user_name
num_total = len(images)
num_done = len(st.session_state.user_done_names)



if num_total == 0:
    st.error(f"没有在文件夹 '{IMAGE_DIR}' 中找到图片。")
    st.stop()

side1, side2 = st.sidebar.columns(2)

with side1:
    if st.button("刷新当前用户记录", use_container_width=True):
        load_user_state(user_name)
        next_idx = get_random_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
        st.session_state.pending_comment_reset = True
        st.rerun()

with side2:
    if st.button("跳到未标注", use_container_width=True):
        next_idx = get_random_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
        st.session_state.pending_comment_reset = True
        st.rerun()

st.sidebar.markdown(f"**当前用户：** {user_name}")
st.sidebar.markdown(f"**已完成：** {num_done} / {num_total}")

if num_done >= num_total and num_total > 0:
    st.sidebar.success("该用户已完成全部图片。")

if st.session_state.user_records:
    csv_df = pd.DataFrame(st.session_state.user_records)
    csv_bytes = csv_df.to_csv(index=False).encode("utf-8-sig")
    st.sidebar.download_button(
        "导出当前用户 CSV",
        data=csv_bytes,
        file_name=f"annotations_{user_name}.csv",
        mime="text/csv",
    )

# ============================================================
# 当前图片
# ============================================================
current_index = max(0, min(st.session_state.current_index, num_total - 1))
st.session_state.current_index = current_index
current_image = images[current_index]
existing = st.session_state.user_record_map.get(current_image.name)

if st.session_state.pending_comment_reset:
    st.session_state.comment_value = ""
    st.session_state.pending_comment_reset = False
elif existing and st.session_state.comment_value == "":
    old_comment = str(existing.get("comment", "")).strip()
    if old_comment:
        st.session_state.comment_value = old_comment

# ============================================================
# 主界面
# ============================================================
main_left, main_right = st.columns([4.6, 1.8])

with main_left:
    info1, info2, info3 = st.columns(3)
    with info1:
        st.markdown("### 当前图片")
        st.write(f"{current_index + 1} / {num_total}")
    with info2:
        st.markdown("### 文件名")
        st.write(f"`{current_image.name}`")
    with info3:
        st.markdown("### 状态")
        if existing:
            st.success(f"已标注：{existing.get('label', '')}")
        else:
            st.info("未标注")

    st.markdown("### 图片")
    try:
        img = Image.open(current_image)
        st.image(img, caption=current_image.name, use_container_width=True)
    except Exception as e:
        st.error(f"图片读取失败：{e}")

with main_right:
    st.markdown("### 导航")
    nav1, nav2 = st.columns(2)

    with nav1:
        if st.button("上一张", use_container_width=True):
            prev_idx = get_previous_marked_index(
                images=images,
                current_image_name=current_image.name,
                marked_history=st.session_state.marked_history,
            )
            if prev_idx is not None:
                st.session_state.current_index = prev_idx
            st.session_state.pending_comment_reset = True
            st.rerun()

    with nav2:
        if st.button("下一张", use_container_width=True):
            next_idx = get_random_unlabeled_index(images, st.session_state.user_done_names)
            if next_idx is not None:
                st.session_state.current_index = next_idx
            st.session_state.pending_comment_reset = True
            st.rerun()

    st.markdown("### 快速跳转")
    jump_number = st.number_input(
        "跳到图片编号（如 123 -> 123.png）",
        min_value=1,
        value=int(current_image.stem) if current_image.stem.isdigit() else 1,
        step=1,
    )

    if st.button("跳转", use_container_width=True):
        target_idx = get_index_by_exact_image_number(images, int(jump_number))
        if target_idx is not None:
            st.session_state.current_index = target_idx
            st.session_state.pending_comment_reset = True
            st.rerun()
        else:
            st.warning(f"没有找到文件名为 {int(jump_number)} 的图片（如 {int(jump_number)}.png）")

    st.markdown("### 分类")
    cls_row1 = st.columns(3)
    cls_row2 = st.columns(3)
    clicked_label = None

    row1_labels = LABELS[:3]
    row2_labels = LABELS[3:]

    for i, label in enumerate(row1_labels):
        with cls_row1[i]:
            if st.button(label, key=f"cls_top_{label}", use_container_width=True):
                clicked_label = label

    for i, label in enumerate(row2_labels):
        with cls_row2[i]:
            if st.button(label, key=f"cls_bottom_{label}", use_container_width=True):
                clicked_label = label

    st.markdown("### 备注")
    comment = st.text_input("备注（可选）", key="comment_value")

if clicked_label is not None:
    save_annotation(user_name, str(current_image), clicked_label, comment)
    st.session_state.last_saved_message = f"已保存：{current_image.name} → {clicked_label}"

    next_idx = get_random_unlabeled_index(images, st.session_state.user_done_names)
    if next_idx is not None:
        st.session_state.current_index = next_idx

    st.session_state.pending_comment_reset = True
    st.rerun()

if st.session_state.last_saved_message:
    st.success(st.session_state.last_saved_message)
