import streamlit as st
from pathlib import Path
from datetime import datetime

import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from PIL import Image


IMAGE_DIR = "3band"  
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
LABELS = ["inner", "outer", "nuclear", "unclear", "not_ring", "skip"]
GSHEET_ID = "1MAKgvgP0vFVTPpjLWmWhDKODEZHkP1ZRk7uVipAYsIs"


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

    base_dir = Path(__file__).resolve().parent
    cred_path = base_dir / "credentials.json"

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        str(cred_path), scope
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GSHEET_ID).sheet1
    return sheet


@st.cache_data(ttl=60, show_spinner=False)
def fetch_user_records_from_gsheet(user_name: str):
    try:
        sheet = init_gsheet()
        records = sheet.get_all_records()
        user_records = [
            r for r in records
            if str(r.get("user_name", "")).strip() == user_name
        ]
        return user_records
    except Exception:
        return []


def load_user_state(user_name: str):
    user_records = fetch_user_records_from_gsheet(user_name)
    user_done_names = {str(r.get("image_name", "")).strip() for r in user_records}
    user_record_map = {
        str(r.get("image_name", "")).strip(): r
        for r in user_records
    }

    st.session_state.user_records = user_records
    st.session_state.user_done_names = user_done_names
    st.session_state.user_record_map = user_record_map


def save_annotation(user_name: str, image_path: str, label: str, comment: str):
    sheet = init_gsheet()
    image_name = Path(image_path).name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [user_name, image_name, label, comment, now]
    sheet.append_row(row)

    # 保存后立刻更新当前 session，避免再次整表读取
    record = {
        "user_name": user_name,
        "image_name": image_name,
        "label": label,
        "comment": comment,
        "timestamp": now,
    }

    if "user_records" not in st.session_state:
        st.session_state.user_records = []
    if "user_done_names" not in st.session_state:
        st.session_state.user_done_names = set()
    if "user_record_map" not in st.session_state:
        st.session_state.user_record_map = {}

    st.session_state.user_records.append(record)
    st.session_state.user_done_names.add(image_name)
    st.session_state.user_record_map[image_name] = record

    fetch_user_records_from_gsheet.clear()



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


def get_next_unlabeled_index(images, done_names):
    for i, p in enumerate(images):
        if p.name not in done_names:
            return i
    return 0 if images else None



images = load_images()

if "user_name" not in st.session_state:
    st.session_state.user_name = ""
if "last_user_name" not in st.session_state:
    st.session_state.last_user_name = ""
if "current_index" not in st.session_state:
    st.session_state.current_index = 0
if "comment_cache" not in st.session_state:
    st.session_state.comment_cache = ""
if "last_saved_message" not in st.session_state:
    st.session_state.last_saved_message = ""
if "user_records" not in st.session_state:
    st.session_state.user_records = []
if "user_done_names" not in st.session_state:
    st.session_state.user_done_names = set()
if "user_record_map" not in st.session_state:
    st.session_state.user_record_map = {}


# ============================================================
# 侧边栏：用户
# ============================================================
st.sidebar.header("User")
user_name = st.sidebar.text_input("请输入用户名", value=st.session_state.user_name).strip()

if user_name:
    if user_name != st.session_state.last_user_name:
        st.session_state.user_name = user_name
        st.session_state.last_user_name = user_name
        st.session_state.comment_cache = ""
        load_user_state(user_name)
        next_idx = get_next_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
        st.rerun()
    else:
        st.session_state.user_name = user_name

if not st.session_state.user_name:
    st.info("请先在左侧输入用户名。")
    st.stop()

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
        next_idx = get_next_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
        st.session_state.comment_cache = ""
        st.rerun()
with side2:
    if st.button("跳到未标注", use_container_width=True):
        next_idx = get_next_unlabeled_index(images, st.session_state.user_done_names)
        if next_idx is not None:
            st.session_state.current_index = next_idx
            st.session_state.comment_cache = ""
            st.rerun()

st.sidebar.markdown(f"**当前用户：** {user_name}")
st.sidebar.markdown(f"**已完成：** {num_done} / {num_total}")

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
# 主界面
# ============================================================
current_index = max(0, min(st.session_state.current_index, num_total - 1))
st.session_state.current_index = current_index
current_image = images[current_index]
existing = st.session_state.user_record_map.get(current_image.name)

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
            st.session_state.current_index = max(0, current_index - 1)
            st.session_state.comment_cache = ""
            st.rerun()
    with nav2:
        if st.button("下一张", use_container_width=True):
            st.session_state.current_index = min(num_total - 1, current_index + 1)
            st.session_state.comment_cache = ""
            st.rerun()

    st.markdown("### 快速跳转")
    jump_index = st.number_input(
        "跳到第几张",
        min_value=1,
        max_value=num_total,
        value=current_index + 1,
        step=1,
    )
    if st.button("跳转", use_container_width=True):
        st.session_state.current_index = int(jump_index) - 1
        st.session_state.comment_cache = ""
        st.rerun()

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
    if existing and not st.session_state.comment_cache:
        prefill_comment = str(existing.get("comment", ""))
    else:
        prefill_comment = st.session_state.comment_cache

    comment = st.text_input("备注（可选）", value=prefill_comment)
    st.session_state.comment_cache = comment

if clicked_label is not None:
    save_annotation(user_name, str(current_image), clicked_label, comment)
    st.session_state.last_saved_message = f"已保存：{current_image.name} → {clicked_label}"

    next_idx = get_next_unlabeled_index(images, st.session_state.user_done_names)
    if next_idx is not None:
        st.session_state.current_index = next_idx
    st.session_state.comment_cache = ""
    st.rerun()

if st.session_state.last_saved_message:
    st.success(st.session_state.last_saved_message)
