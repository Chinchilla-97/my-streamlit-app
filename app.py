import hmac
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

import streamlit as st

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
UPLOAD_DIR = "activity_logs"
ALLOWED_EXT = {".hwp", ".hwpx", ".jpg", ".jpeg", ".png", ".pdf"}
ACTIVITY_TYPES = ["대면", "비대면"]
MAX_FILE_MB = 20

os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_secret(key: str, fallback: str) -> str:
    """배포 시에는 .streamlit/secrets.toml 에 값을 넣어두고 사용."""
    try:
        return st.secrets[key]
    except Exception:
        return fallback


PASSWORD = get_secret("APP_PASSWORD", "change-me")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "change-me-admin")


def check_password(entered: str, real: str) -> bool:
    # 한글 등 비ASCII 문자를 쓰려면 바이트로 변환해서 비교해야 함
    return hmac.compare_digest(
        str(entered).encode("utf-8"), str(real).encode("utf-8")
    )


def safe_name(text: str) -> str:
    """경로 조작·특수문자 제거. 한글은 유지."""
    text = unicodedata.normalize("NFC", str(text)).strip()
    text = os.path.basename(text.replace("\\", "/"))
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = text.strip("._")
    return text[:80] or "unnamed"


def unique_path(directory: str, filename: str) -> str:
    """제출 시각(HHMM)을 붙여 저장. 그래도 겹치면 (1), (2) … 를 덧붙임."""
    stem, ext = os.path.splitext(filename)
    stem = f"{stem}_{datetime.now():%H%M}"
    candidate = os.path.join(directory, f"{stem}{ext}")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}({counter}){ext}")
        counter += 1
    return candidate


# ----------------------------------------------------------------------
# 로그인
# ----------------------------------------------------------------------
st.title("📂 활동가 활동일지 제출 시스템")

st.session_state.setdefault("authenticated", False)

if not st.session_state.authenticated:
    pw = st.text_input("접속 비밀번호를 입력하세요", type="password")
    if st.button("로그인"):
        if check_password(pw, PASSWORD):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

st.success("로그인 성공")
if st.button("로그아웃"):
    st.session_state.authenticated = False
    st.rerun()

# ----------------------------------------------------------------------
# 제출 폼
# ----------------------------------------------------------------------
with st.form("log_upload_form", clear_on_submit=True):
    activist_name = st.text_input("활동가 성함 (본인 이름 입력)")
    log_date = st.date_input(
        "활동 일자 (지난 날짜도 선택 가능)",
        value=date.today(),
        min_value=date.today() - timedelta(days=730),
        max_value=date.today(),
        format="YYYY-MM-DD",
    )
    activity_type = st.radio(
        "활동 종류",
        options=ACTIVITY_TYPES,
        horizontal=True,
    )
    uploaded_files = st.file_uploader(
        f"파일 첨부 (HWP, HWPX, JPG, PNG, PDF · 파일당 최대 {MAX_FILE_MB}MB)",
        type=[e.lstrip(".") for e in sorted(ALLOWED_EXT)],
        accept_multiple_files=True,
    )
    submitted = st.form_submit_button("제출하기")

if submitted:
    if not activist_name.strip() or not uploaded_files:
        st.warning("이름과 파일을 모두 입력해주세요.")
    else:
        date_str = log_date.strftime("%Y%m%d")
        name_part = safe_name(activist_name)
        saved, skipped = [], []

        for uploaded_file in uploaded_files:
            original = safe_name(uploaded_file.name)
            ext = os.path.splitext(original)[1].lower()

            if ext not in ALLOWED_EXT:
                skipped.append(f"{uploaded_file.name} (허용되지 않는 형식)")
                continue
            if uploaded_file.size > MAX_FILE_MB * 1024 * 1024:
                skipped.append(f"{uploaded_file.name} (용량 초과)")
                continue

            target = unique_path(
                UPLOAD_DIR,
                f"{date_str}_{activity_type}_{name_part}_{original}",
            )
            try:
                with open(target, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                saved.append(os.path.basename(target))
            except OSError as err:
                skipped.append(f"{uploaded_file.name} (저장 실패: {err})")

        if saved:
            st.success(f"{activist_name}님, {len(saved)}개 파일이 제출되었습니다.")
            for name in saved:
                st.caption(f"저장됨: {name}")
        if skipped:
            st.error("제출되지 않은 파일:\n" + "\n".join(f"- {s}" for s in skipped))

# ----------------------------------------------------------------------
# 관리자
# ----------------------------------------------------------------------
with st.expander("관리자 확인용"):
    admin_pw = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
    if admin_pw and check_password(admin_pw, ADMIN_PASSWORD):
        st.subheader("📋 전체 제출 현황")
        files = sorted(os.listdir(UPLOAD_DIR), reverse=True)
        if not files:
            st.info("아직 제출된 파일이 없습니다.")

        counts = {t: sum(f"_{t}_" in n for n in files) for t in ACTIVITY_TYPES}
        st.caption(
            "  ·  ".join(f"{t} {c}건" for t, c in counts.items())
            + f"  ·  전체 {len(files)}건"
        )

        choice = st.radio(
            "활동 종류로 보기",
            options=["전체"] + ACTIVITY_TYPES,
            horizontal=True,
            key="admin_filter",
        )
        if choice != "전체":
            files = [n for n in files if f"_{choice}_" in n]
            if not files:
                st.info(f"{choice} 활동 제출 내역이 없습니다.")

        for name in files:
            path = os.path.join(UPLOAD_DIR, name)
            size_kb = os.path.getsize(path) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            col1, col2 = st.columns([4, 1])
            col1.write(f"{name}  ·  {size_kb:,.0f}KB  ·  {mtime:%Y-%m-%d %H:%M}")
            with open(path, "rb") as f:
                col2.download_button("받기", f.read(), file_name=name, key=path)
    elif admin_pw:
        st.error("관리자 비밀번호가 다릅니다.")
